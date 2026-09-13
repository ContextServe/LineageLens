import * as vscode from 'vscode'
import { execSync } from 'child_process'
import * as fs from 'fs'
import * as path from 'path'

export function activate(context: vscode.ExtensionContext) {
  const outputChannel = vscode.window.createOutputChannel('LineageLens')

  // Analyze workspace command
  const analyzeCmd = vscode.commands.registerCommand('lineagelens.analyzeWorkspace', async () => {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0]
    if (!workspaceFolder) {
      vscode.window.showErrorMessage('No workspace folder open')
      return
    }

    try {
      outputChannel.show()
      outputChannel.appendLine('🔍 Running LineageLens analysis...')

      // Schema 4: one command, no init step and no config file.
      const cmd = `lineagelens index "${workspaceFolder.uri.fsPath}"`
      const result = execSync(cmd, { encoding: 'utf-8' })

      outputChannel.appendLine(result)
      outputChannel.appendLine('✅ Analysis complete!')

      // Try to parse report
      const reportPath = path.join(workspaceFolder.uri.fsPath, '.lineagelens', 'report.json')
      if (fs.existsSync(reportPath)) {
        const report = JSON.parse(fs.readFileSync(reportPath, 'utf-8'))

        // Create diagnostics from failures
        const diags = new Map<string, vscode.Diagnostic[]>()
        for (const failure of report.failures) {
          const filePath = path.join(workspaceFolder.uri.fsPath, failure.file)
          if (!diags.has(filePath)) {
            diags.set(filePath, [])
          }

          const range = new vscode.Range(failure.line - 1, 0, failure.line - 1, 100)
          const diag = new vscode.Diagnostic(
            range,
            `[${failure.stage}] ${failure.error_type}: ${failure.message}`,
            vscode.DiagnosticSeverity.Warning
          )
          diags.get(filePath)!.push(diag)
        }

        // Publish diagnostics
        const collection = vscode.languages.createDiagnosticCollection('lineagelens')
        for (const [filePath, diagnostics] of diags) {
          collection.set(vscode.Uri.file(filePath), diagnostics)
        }

        if (report.failures.length > 0) {
          vscode.window.showWarningMessage(`Analysis complete with ${report.failures.length} issues`)
        } else {
          vscode.window.showInformationMessage('Analysis complete! No issues found.')
        }
      }
    } catch (error: any) {
      outputChannel.appendLine(`❌ Error: ${error.message}`)
      vscode.window.showErrorMessage('LineageLens analysis failed. Check output for details.')
    }
  })

  // Show lineage at cursor
  const showLineageCmd = vscode.commands.registerCommand('lineagelens.showLineageAtCursor', async () => {
    const editor = vscode.window.activeTextEditor
    if (!editor) return

    const position = editor.selection.active
    const word = editor.document.getWordRangeAtPosition(position)
    if (!word) return

    const symbolName = editor.document.getText(word)

    // Open the visualization panel showing this symbol
    vscode.commands.executeCommand('lineagelens.showPanel')

    outputChannel.appendLine(`📍 Showing lineage for: ${symbolName} at line ${position.line + 1}`)
  })

  // Open visualization panel
  const showPanelCmd = vscode.commands.registerCommand('lineagelens.showPanel', async () => {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0]
    if (!workspaceFolder) {
      vscode.window.showErrorMessage('No workspace folder open')
      return
    }

    // Check if server is already running
    const serverPort = 8717
    const serverUrl = `http://127.0.0.1:${serverPort}`

    try {
      // Try to connect
      const fetch = require('node-fetch')
      await fetch(`${serverUrl}/api/v1/entry-points`, { timeout: 2000 })
    } catch {
      // Server not running, start it
      outputChannel.appendLine('Starting LineageLens server...')
      try {
        const cmd = `lineagelens serve "${workspaceFolder.uri.fsPath}"`
        // Run in background (non-blocking)
        const { spawn } = require('child_process')
        spawn('sh', ['-c', cmd], { detached: true, stdio: 'ignore' })

        // Wait a bit for server to start
        await new Promise((resolve) => setTimeout(resolve, 2000))
      } catch (error) {
        vscode.window.showErrorMessage('Failed to start LineageLens server. Install with: pip install lineagelens[web]')
        return
      }
    }

    // Create webview panel
    const panel = vscode.window.createWebviewPanel(
      'lineagelens',
      'LineageLens Code Graph',
      vscode.ViewColumn.Beside,
      { enableScripts: true }
    )

    panel.webview.html = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LineageLens</title>
  <style>
    body { margin: 0; padding: 0; }
    iframe { width: 100%; height: 100vh; border: none; }
  </style>
</head>
<body>
  <iframe src="${serverUrl}/"></iframe>
</body>
</html>
    `
  })

  context.subscriptions.push(analyzeCmd, showLineageCmd, showPanelCmd)

  console.log('LineageLens extension activated')
}

export function deactivate() {}
