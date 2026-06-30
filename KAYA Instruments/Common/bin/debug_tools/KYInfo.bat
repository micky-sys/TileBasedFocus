:: BatchGotAdmin
::-------------------------------------
@goto bypass_permissions
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

:bypass_permissions

cd /d "%PROGRAMDATA%\KAYA Instruments\"
del KAYA.zip
mkdir .\tmp
cd .\tmp\
Robocopy /s "%PROGRAMDATA%\KAYA Instruments\Logs" .\Logs
Robocopy /s "%KAYA_VISION_POINT_2_BIN_PATH%\..\..\Log" .\Installation_logs
Msinfo32 /nfo .\system_info.nfo
Msinfo32 /report .\system_info.txt
echo ######### CPU Usage ########## > resources.txt
wmic cpu get loadpercentage | more >> resources.txt
echo ############################## >> resources.txt
echo. >> resources.txt
echo ######## Memory Usage ######## >> resources.txt
wmic os get FreePhysicalMemory, TotalVisibleMemorySize | more >> resources.txt
echo ############################## >> resources.txt
echo. >> resources.txt
echo ######### Disk Usage ######### >> resources.txt
wmic logicaldisk get caption, freeSpace, size | more >> resources.txt
echo ############################## >> resources.txt
dir "%PROGRAMDATA%\boost_interprocess" /b /s /a-d > boost_interprocess.txt
REG QUERY "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v "HiberbootEnabled" | Find "0x1" >nul 2>&1
IF %ERRORLEVEL% == 0 (echo "Fast Startup is ON" > fast_startup_status.txt) else (echo "Fast Startup is OFF" > fast_startup_status.txt)
echo Windows Registry Editor Version 5.00 > KAYA_settings.reg
SET keys= "HKCU\SOFTWARE\KAYA Instruments (Service Controller)" "HKCU\SOFTWARE\KAYA Instruments" "HKLM\SOFTWARE\KAYA Instruments (Service)"
for %%a in (%keys%) do (
    ::echo %%a
    reg export %%a temp.reg /y > nul
    MORE +1 temp.reg >> KAYA_settings.reg
    del temp.reg
)
tar -a -c -f ../KAYA.zip *
cd ..
rmdir /s /q tmp
explorer /select,"KAYA.zip"

