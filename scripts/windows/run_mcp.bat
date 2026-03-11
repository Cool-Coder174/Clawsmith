@echo off
:: ClawSmith — Start MCP Server (alias for start_mcp_server.bat)
call "%~dp0start_mcp_server.bat"
exit /b %ERRORLEVEL%
