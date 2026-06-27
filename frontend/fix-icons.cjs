const fs = require('fs');
const path = require('path');
const replacements = {
  'AlertTriangle': 'Warning',
  'ArrowUpDown': 'ArrowsDownUp',
  'ChevronLeft': 'CaretLeft',
  'ChevronRight': 'CaretRight',
  'CheckCircle2': 'CheckCircle',
  'GitCompare': 'GitDiff',
  'Search': 'MagnifyingGlass',
  'Loader2': 'CircleNotch',
  'TrendingUp': 'TrendUp',
  'TrendingDown': 'TrendDown',
};
function processDir(dir) {
  const files = fs.readdirSync(dir);
  for (const file of files) {
    const fullPath = path.join(dir, file);
    if (fs.statSync(fullPath).isDirectory()) {
      processDir(fullPath);
    } else if (fullPath.endsWith('.tsx') || fullPath.endsWith('.ts')) {
      let content = fs.readFileSync(fullPath, 'utf8');
      if (content.includes('lucide-react')) {
        content = content.replace(/lucide-react/g, '@phosphor-icons/react');
        for (const [lucide, phosphor] of Object.entries(replacements)) {
          const regex = new RegExp(`\\b${lucide}\\b`, 'g');
          content = content.replace(regex, phosphor);
        }
        fs.writeFileSync(fullPath, content);
        console.log('Fixed', fullPath);
      }
    }
  }
}
processDir('./src');
