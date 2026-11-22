# Scripting PlanetPDF: command line, local API and plugins

Everything below works on Windows, the Mac and Linux. Programs may use every function;
**signing always asks you first**, in a PlanetPDF window.

## The command line

```
planetpdf help                      the commands, by group
planetpdf help export               one command's options
planetpdf commands --json           every command and parameter, for programs
planetpdf info report.pdf
planetpdf export report.pdf --format docx --out report.docx
planetpdf redact report.pdf --preset ssn --find "John Q. Public" --overlay-text REDACTED --out report_redacted.pdf
planetpdf compress scan.pdf --quality small --out scan_small.pdf
planetpdf fill form.pdf --set "Name=Jane Doe" --set Agree=on --flatten --out filled.pdf
planetpdf sign contract.pdf --id <thumbprint from signing-ids> --out contract_signed.pdf
```

- **Where:** on Windows the installer can add PlanetPDF to the PATH (ticked by default), so
  `planetpdf` works in any command prompt or PowerShell (it runs `PlanetPDF.com` in the install
  folder). On the Mac: `/Applications/PlanetPDF.app/Contents/MacOS/PlanetPDF <command> …`. From
  the source: `python -m planetpdf <command> …`.
- **Output:** a readable summary, or the result as JSON with `--json`. Progress ("Reading page 3
  of 10") goes to stderr; `--quiet` turns it off.
- **Exit codes:** 0 done · 1 it failed (the message says why) · 2 the command was used wrongly ·
  3 signing wasn't approved · 4 done, but a check failed (redaction verification, compliance) ·
  130 stopped (Ctrl+C).
- **Files:** a command never writes over an existing file unless you add `--overwrite`; it writes
  over its own input only when `--out` names it.
- **Pages** are typed as in the program: `"1-3, 7, 10-"`.
- **Passwords:** `--password -` asks for it (not shown as you type); `--password env:NAME` reads
  the environment variable NAME. A password typed on the command line itself can be seen by other
  programs on the computer.
- **Signing** shows a PlanetPDF window asking whether to allow it, every time.

## The local API (for programs)

JSON-RPC 2.0, one JSON object per line (UTF-8), over a connection only programs on this computer,
running as you, can open: a named pipe on Windows, a socket file on the Mac and Linux. Never a
network port.

- **Where it is:** the running PlanetPDF writes `api.json` (`{"address": …}`) into its local app
  data folder (Windows `%LOCALAPPDATA%\Planet Networks\PlanetPDF`, Mac `~/Library/Application
  Support/Planet Networks/PlanetPDF`, Linux `~/.local/share/Planet Networks/PlanetPDF`). Without a
  PlanetPDF window, `planetpdf serve` serves it until Ctrl+C.
- **Python:** copy `resources/sdk/planetpdf_client.py` (standard library only):

  ```python
  from planetpdf_client import PlanetPDF
  with PlanetPDF("Invoice Mailer", publisher="Accounting", token_file="planetpdf.token") as pdf:
      pdf.call("compress", file="in.pdf", out="out.pdf", on_progress=lambda p: print(p["text"]))
  ```

- **Other languages:** open the address (Windows: the pipe as a file; elsewhere a Unix socket),
  write a line, read lines:
  1. `{"jsonrpc":"2.0","id":1,"method":"planetpdf.hello","params":{"name":"My Program","publisher":"Me"}}`
     The first time the result has a `token`: keep it and send `{"token": …}` instead next time.
  2. `{"jsonrpc":"2.0","id":2,"method":"export","params":{"file":"a.pdf","format":"docx","out":"a.docx"}}`
     Every command of the command line is a method of the same name, its options the params
     (`planetpdf.commands` lists them). Before the result, long work sends
     `{"jsonrpc":"2.0","method":"progress","params":{"id":2,"done":3,"total":10,"text":"Reading page 4 of 10"}}`.
  3. `planetpdf.cancel` with `{"id": 2}` stops a job. In the PlanetPDF window, `ui.open`
     (`{"files": […]}`) opens files there and `ui.current_document` says what's open.
- **Errors:** -32000 the work failed · -32001 say hello first · -32002 signing not approved ·
  -32003 blocked in Preferences · -32004 a plugin used something it didn't ask for · -32005 too
  many requests (10 a second, 2 jobs at once) · -32006 canceled · -32007 the user didn't allow
  this program · -32601 no such method ·
  -32602 bad parameters.
- **Your control:** Preferences ▸ Scripting & Plugins lists every program that has used PlanetPDF:
  Block, Stop Signing (it's asked again next time), Remove; **Activity Log** shows what each one
  did (which files, how it ended; never passwords). The API can be turned off there.
- **Allowing a program:** the first time a program says hello, PlanetPDF asks you whether it may
  use PlanetPDF, naming the program file it really is (looked up from the operating system, not
  taken from the name it gives). Its token then works only for that program file: another
  program using the token (or the same program moved or updated) is asked about again.
  Refused programs aren't asked about again until PlanetPDF restarts. Plugins aren't asked about:
  you turned them on.
- **Signing:** the first time a program signs, you're asked: Allow Once, Always Allow This
  Program (until you Stop Signing), or Don't Allow.

## Plugins

A plugin is a folder in `<profile>\plugins` (Windows `%APPDATA%\Planet Networks\PlanetPDF\plugins`)
or in a folder IT lists in `PLANETPDF_PLUGINS`, with a `plugin.json`:

```json
{
  "id": "com.example.invoice-stamper",
  "name": "Invoice Stamper",
  "version": "1.0",
  "publisher": "Example Accounting",
  "description": "Stamps the open invoice and files it.",
  "api": 1,
  "run": ["python", "stamper.py"],
  "permissions": ["read", "write"],
  "menu_items": [{"menu": "Tools", "title": "File Invoice", "command": "file", "needs_document": true}]
}
```

- **Nothing runs until you turn it on** in Preferences ▸ Scripting & Plugins, which shows what it
  asks for. If any of its files change afterwards it's off again until you turn it on again.
- **Menus:** its items go at the end of the menus it names (File, Edit, View, Tools, Window, Help).
- **Running:** choosing an item starts `run` (the program is looked for in the plugin's folder
  first) in its own process and folder, with `PLANETPDF_API_ADDRESS`, `PLANETPDF_API_TOKEN`,
  `PLANETPDF_COMMAND`, and `PLANETPDF_DOCUMENT` / `PLANETPDF_PAGE` when a document is open. In
  Python: `PlanetPDF.from_environment()`. Its output goes to `<profile>\logs\plugins\<id>.log`.
- **Permissions:** `read` (details, text, fields, searches, checks), `write` (make and change
  documents), `sign` (sign; you're still asked). The API refuses anything else (-32004).
- **What permissions don't do:** a plugin is a program running as you, like any program you start.
  The permissions limit what it can do *through PlanetPDF*, not what it can do on its own. Turn on
  only plugins from people you trust.

An example: `resources/sdk/example-plugin`.
