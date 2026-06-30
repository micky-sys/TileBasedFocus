@echo off
:: BatchGotAdmin
::-------------------------------------
::@goto do_as_admin

:: MAX attempts to check the status of operations
set MAX_ATTEMPTS=5 
:: Bypass 'Check for permissions' if already called from UAC.ShellExecute below
IF "%1"=="fromshell" GOTO do_as_admin

@REM  --> Check for permissions
@>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"

@REM --> If error flag set, we do not have admin.
if '%errorlevel%' NEQ '0' (
    goto UACPrompt
) else ( goto do_as_admin )


:UACPrompt
    @echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    @set params = %*:"="
    @echo UAC.ShellExecute "cmd.exe", "/c %~s0 %params% fromshell", "", "runas", 1 >> "%temp%\getadmin.vbs"
    @echo Dim timeout >> "%temp%\getadmin.vbs"
    @echo timeout = 70 >> "%temp%\getadmin.vbs"
    @echo Dim startTime, currentTime >> "%temp%\getadmin.vbs"
    @echo startTime = Timer >> "%temp%\getadmin.vbs"
    @echo Set objFSO = CreateObject("Scripting.FileSystemObject") >> "%temp%\getadmin.vbs"
    @echo Do While Not objFSO.FileExists("%temp%\KYRestartDone.txt") And Timer - startTime ^< timeout  >> "%temp%\getadmin.vbs"
    @echo WScript.Sleep 1000 >> "%temp%\getadmin.vbs"
    @echo Loop >> "%temp%\getadmin.vbs"
    @echo If objFSO.FileExists("%temp%\KYRestartDone.txt") Then >> "%temp%\getadmin.vbs"
    @echo  objFSO.DeleteFile("%temp%\KYRestartDone.txt"), True >> "%temp%\getadmin.vbs"
    @echo Else >> "%temp%\getadmin.vbs"
    @echo WScript.Echo "Timeout reached" >> "%temp%\getadmin.vbs"
    @echo WScript.Quit(1) >> "%temp%\getadmin.vbs"
    @echo End If >> "%temp%\getadmin.vbs"

    echo Starting script as admin
    cscript //NoLogo "%temp%\getadmin.vbs"
    if errorlevel 1 (
    echo Problems with the admin part of the script.
    exit /B 1
    ) else ( goto do_as_user)


:do_as_admin
    :: 1. Stop Service Controller
    echo Stopping Service Controller
    "%KAYA_VISION_POINT_BIN_PATH%\KYServiceClientConsole_vc141.exe" /address=KYTray /request=T:Shutdown: /delay=1000
    set /a ATTEMPT=0
    :check_service_controller_stop
    set /a ATTEMPT+=1
    tasklist /fi "IMAGENAME eq VisionPoint.exe" 2>NUL | find /i "VisionPoint.exe" >NUL
    if %errorlevel% EQU 0 (
            echo Service controller is running, stopping now...
            taskkill /f /t /im VisionPoint.exe
            if %ATTEMPT% lss %MAX_ATTEMPTS% (
                goto check_service_controller_stop   
            ) else (
                echo Reached max attempts to stop KAYA Service Controller
                exit /b 1
            )
    ) else (
        echo Service controller is not running
    )

    :: 2. Stop Service

    echo Stopping KAYA Service
    "%KAYA_VISION_POINT_BIN_PATH%\KYServiceClientConsole_vc141.exe" /address=KYDaemon /request=T:Shutdown: /delay=1000
    set /a ATTEMPT=0
    :check_service_stop
    set /a ATTEMPT+=1
    sc query "KYService" | findstr /I /C:"RUNNING"
    if errorlevel 1 (
        echo KAYA Service is not running.
    ) else (
        echo Attempts: %ATTEMPT% KAYA Service is running, stopping now...
        net stop "KYService"
        if %ATTEMPT% lss %MAX_ATTEMPTS% (
            goto check_service_stop   
        ) else (
            echo Reached max attempts to stop KAYA Service
            exit /b 1
        )
    )
    ::pause

    :: 4. Clean the boost_interprocess folder
    echo Cleaning the boost_interprocess folder
    for /r "%programdata%\boost_interprocess" %%i in (KAYA*) do (
        echo Deleting: %%i
        del "%%i"
    )
    ::pause

    :: 5. Start Service
    echo Starting KAYA Service
    sc start KYService
    set /a ATTEMPT=0
    :check_service_start
    sc query "KYService" | findstr /I /C:"RUNNING"
    if errorlevel 0 (
        echo KAYA Service is running.
     ) else (
            echo Attempts: %ATTEMPT% KAYA Service is not running, starting now...
            net start "KYService"
            if %ATTEMPT% lss %MAX_ATTEMPTS% (
            goto check_service_start   
            ) else (
                echo Reached max attempts to start KAYA Service
                exit /b 1
            )
     )     
    ::pause

    :: Create the Done flag for VBS
    echo Done > "%temp%\KYRestartDone.txt"
    ::pause
    exit /B
    
:do_as_user
    :: 6 Start Service Controller
    "%KAYA_VISION_POINT_BIN_PATH%\KYServiceClientConsole_vc141.exe" /address=KYDaemon /request=J:RequestState: /timeout=5000
    ::pause
    echo Starting Service Controller
    start "" "%KAYA_VISION_POINT_APP_PATH%\VisionPoint.exe" -d
    set /a ATTEMPT=0
    :check_service_controller_start
    set /a ATTEMPT+=1
    tasklist /fi "IMAGENAME eq VisionPoint.exe" 2>NUL | find /i "VisionPoint.exe" >NUL
    if %errorlevel% EQU 0 (
            echo Service controller is running
    ) else (
        start "" "%KAYA_VISION_POINT_APP_PATH%\VisionPoint.exe" -d
        if %ATTEMPT% lss %MAX_ATTEMPTS% (
                goto check_service_controller_start   
            ) else (
                echo Reached max attempts to start KAYA Service Controller
                exit /b 1
            )
    )
    ::pause    
    exit /B




