import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Provider } from "react-redux";
import { BrowserRouter } from "react-router-dom";
import Phase1App from "./Phase1App";
import { phase1Store } from "./store/phase1Store";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Provider store={phase1Store}>
      <BrowserRouter>
        <Phase1App />
      </BrowserRouter>
    </Provider>
  </StrictMode>,
);
