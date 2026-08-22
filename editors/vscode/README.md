# LineageLens for VS Code

Interactive Python code graph visualization in VS Code.

## Features

- **Analyze Workspace**: `Ctrl+Shift+P` → "LineageLens: Analyze Workspace"
- **Show Lineage**: `Ctrl+Shift+L` on any symbol to see its call graph
- **Interactive Graph**: Zoom, pan, highlight call flows
- **Problem Integration**: Analysis failures appear in Problems panel
- **Zero Token Cost**: All analysis runs locally

## Installation

### From VS Code Marketplace

1. Open VS Code Extensions (`Ctrl+Shift+X`)
2. Search "LineageLens"
3. Click Install

### From Source

1. Clone the repo
2. `cd editors/vscode && npm install`
3. `npm run build && npm run package`
4. Open the `.vsix` file to install locally

## Setup

1. Install LineageLens: `pip install lineagelens[web]`
2. Open a Python project in VS Code
3. Run: `Cmd+Shift+P` → "LineageLens: Analyze Workspace"
4. Once complete, use `Ctrl+Shift+L` or the command palette to open the visualization

## Commands

| Command | Shortcut | Description |
|---------|----------|-------------|
| **Analyze Workspace** | — | Scan project and build code graph |
| **Show Lineage at Cursor** | `Ctrl+Shift+L` | Visualize code flow for symbol at cursor |
| **Open Visualization** | — | Open the interactive graph panel |

## Settings

Add to VS Code `settings.json`:

```json
{
  "lineagelens.autoAnalyze": true,
  "lineagelens.showProblems": true,
  "lineagelens.serverPort": 8717
}
```

## Troubleshooting

**"LineageLens command not found"**
- Install: `pip install lineagelens[web]`
- Restart VS Code

**"Server failed to start"**
- Check: `lineagelens --help` works
- Make sure port 8717 is available
- Try manually: `lineagelens serve /path/to/project`

**"No graph shown"**
- Run analysis first: `Cmd+Shift+P` → "LineageLens: Analyze Workspace"
- Check `.lineagelens/graph.json` exists in project root

## Publishing

### First Time

1. Get a **VS Code Marketplace Publisher Account**:
   - Go to https://marketplace.visualstudio.com/manage/
   - Sign in with Microsoft account
   - Create new publisher: "lineagelens"

2. Install `vsce`:
   ```bash
   npm install -g vsce
   ```

3. Login:
   ```bash
   vsce login lineagelens
   ```
   (Paste your publisher token when prompted)

4. Package:
   ```bash
   npm run package
   ```

5. Publish:
   ```bash
   npm run publish
   ```

### Subsequent Releases

Update version in `package.json`, then:

```bash
npm run publish
```

## Development

```bash
# Build
npm run build

# Watch mode
npm run watch

# Test locally
npm run package  # Creates .vsix
# Then open the .vsix file to install locally
```

## Support

See main README.md for CLI documentation and deployment guides.
