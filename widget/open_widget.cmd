@echo off
rem Start the widget OUTSIDE the Claude desktop app.
rem The Claude app (MSIX) redirects AppData writes into its own private folder, so a widget or settings window
rem started from Claude saves teacher_config.json where the real widget (desktop/startup shortcut) cannot read it.
rem Claude runs:  Start-Process explorer.exe -ArgumentList "<full path>\widget\open_widget.cmd"
rem ASCII only on purpose: cmd reads .cmd files in the ANSI code page. The escapes below = the widget file name.
cd /d "%~dp0"
start "" "%~dp0..\.venv\Scripts\pythonw.exe" -c "import os,runpy; runpy.run_path(os.path.join(os.getcwd(), '\ubc14\ub85c\uac00\uae30\uc704\uc82f.pyw'), run_name='__main__')"
