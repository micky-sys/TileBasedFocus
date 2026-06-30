:: BatchGotAdmin
::-------------------------------------
@goto bypass_permissins
@REM  --> Check for permissions
@>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"

@REM --> If error flag set, we do not have admin.
@if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    set params = %*:"="
    echo UAC.ShellExecute "cmd.exe", "/c %~s0 %params%", "", "runas", 1 >> "%temp%\getadmin.vbs"

    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    @pushd "%CD%"
    @CD /D "%~dp0"
	
:bypass_permissins

"%~dp0dpinst.exe" /s /d /u KAYAKERN.inf
del /F /Q "%SYSTEMROOT%\system32\drivers\KAYAKERN.sys"
"%~dp0devcon.exe" rescan
