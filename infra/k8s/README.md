# AgentOps Platform — Kubernetes Deployment

## Two ways to run

| Mode | `EXECUTOR_MODE` | Executor | When to use |
|------|-----------------|----------|-------------|
| **Docker** (default) | `docker` | `docker-py` SDK → isolated containers | Local dev, `docker-compose up` |
| **Kubernetes** | `k8s` | `kubernetes` Client → isolated Jobs | Production cluster, `kubectl apply -k` |

## Prerequisites

- Kubernetes cluster (minikube, kind, k3s, or cloud K8s)
- `kubectl` installed and configured
- Ingress controller (e.g., nginx-ingress)
  - minikube: `minikube addons enable ingress`

## Quick Start

```bash
# 1. Set your LLM API key
sed -i 's/your-llm-api-key-here/sk-your-real-key/' secret.yaml

# 2. Build container images
docker build -t agentops-api:latest -f ../../backend/Dockerfile ../../backend
docker build -t agentops-web:prod -f ../../frontend/Dockerfile.prod ../../frontend

# 3. Load into minikube (if using minikube)
minikube image load agentops-api:latest
minikube image load agentops-web:latest

# 4. Deploy
kubectl apply -k .

# 5. Wait for readiness
kubectl wait --for=condition=ready pod -l app=agentops -n agentops --timeout=120s

# 6. Access
# minikube:
minikube service agentops-api -n agentops --url
curl http://<minikube-ip>/api/health

# Or via ingress:
echo "$(minikube ip) agentops.local" | sudo tee -a /etc/hosts
curl http://agentops.local/api/health
```

## How it works

### Docker mode (`EXECUTOR_MODE=docker`)

```
Agent run → DockerExecutor → docker.from_env() → container.run(tool.image) → collect logs
```

Setup: mount `/var/run/docker.sock` into the API container.

### Kubernetes mode (`EXECUTOR_MODE=k8s`)

```
Agent run → K8sJobExecutor → kubernetes client → batch/v1 Job → pod logs
```

Setup: API pod's `serviceAccountName: agentops-executor` (RBAC: create/delete Jobs, read Pods, read Pod logs).

No Docker socket needed — the K8s API server creates Jobs directly.

## Architecture

```
┌──────────────────────────────────────────────┐
│                    Ingress                    │
│  /api → agentops-api:8000                   │
│  /    → agentops-web:80                     │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
┌───▼──────┐      ┌──────▼──────┐
│  API × 2 │      │  Web × 1    │
│  :8000   │      │  nginx :80  │
│  SA:     │      │             │
│  executor│      └─────────────┘
└───┬──────┘
    │ EXECUTOR_MODE=k8s
    │ creates K8s Jobs per tool call
    ▼
┌─────────────┐     ┌─────────────┐
│ agentops-db │     │  Tool Jobs  │
│ PostgreSQL  │     │  (ephemeral)│
│ :5432       │     │  ttl: 300s  │
└─────────────┘     └─────────────┘
```

## Configuration

| ConfigMap Key | Description | Default |
|---------------|-------------|---------|
| `LLM_BASE_URL` | LLM provider endpoint | `https://api.openai.com/v1` |
| `LLM_MODEL` | Model identifier | `gpt-4o` |
| `DATABASE_URL` | PostgreSQL connection | in-cluster `agentops-db` |
| `EXECUTOR_MODE` | `docker` or `k8s` | `k8s` |
| `CONTEXT_WINDOW_LIMIT` | Max context tokens | `128000` |
| `LOG_LEVEL` | Logging verbosity | `info` |

## Secrets

```bash
# Create with real values:
kubectl create secret generic agentops-secret -n agentops \
  --from-literal=POSTGRES_USER=agentops \
  --from-literal=POSTGRES_PASSWORD=your-db-password \
  --from-literal=LLM_API_KEY=sk-your-real-key
```

## Switching between modes

Change `EXECUTOR_MODE` in `configmap.yaml`:

```yaml
# Docker executor (needs docker.sock mount)
EXECUTOR_MODE: "docker"

# K8s Job executor (needs ServiceAccount + RBAC)
EXECUTOR_MODE: "k8s"
```

Apply:

```bash
kubectl apply -f configmap.yaml
kubectl rollout restart deployment/agentops-api -n agentops
```

## Production notes

- Replace `emptyDir` in `deployment-db.yaml` with a `PersistentVolumeClaim` for persistent data
- Set `replicas: 2+` on API for HA
- Use `imagePullPolicy: Always` with a real image registry tag
- Replace `secret.yaml` with a sealed-secret or external-secrets operator
- Add `HorizontalPodAutoscaler` for API: `kubectl autoscale deployment agentops-api --cpu-percent=70 --min=2 --max=10`
