; Doc2Excel installer (NSIS 3). Per-user install, no admin rights needed:
;   %LOCALAPPDATA%\Programs\Doc2Excel\Doc2Excel.exe
; The folder is writable, so the .env settings file can live next to the exe
; as the app expects. An existing .env is never overwritten.
;
; Build (after build.ps1 made dist\Doc2Excel.exe):
;   makensis /DVERSION=1.1.0 installer\doc2excel.nsi
; Output: dist\Doc2Excel-Setup-<version>.exe

Unicode true
; Silent use (the in-app updater runs "Setup.exe /S /RESTART"):
;   /S        no windows; waits up to 30 s for a running Doc2Excel to exit
;   /RESTART  start Doc2Excel again after a successful install

!ifndef VERSION
  !define VERSION "1.1.2"
!endif
; Edition (build.ps1 -Edition passes these). APP_ID names folders, registry
; keys and the single-instance lock; APP_NAME is what the user sees.
!ifndef APP_ID
  !define APP_ID "Doc2Excel"
!endif
!ifndef APP_NAME
  !define APP_NAME "Doc2Excel Pro"
!endif
!define APP "${APP_ID}"
!define EXE "Doc2Excel"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP_NAME}"
OutFile "..\dist\${EXE}-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "..\assets\icon.ico"
UninstallIcon "..\assets\icon.ico"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" ""

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}.exe"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

LangString RunningMsg ${LANG_SIMPCHINESE} "${APP_NAME} 正在运行。请先关闭它的窗口，再按“确定”继续。"
LangString RunningMsg ${LANG_ENGLISH} "${APP_NAME} is running. Close its window, then press OK to continue."

Function .onInit
  ${IfNot} ${Silent}
    !insertmacro MUI_LANGDLL_DISPLAY
  ${EndIf}
FunctionEnd

; /RESTART: the in-app updater closed Doc2Excel to install; open it again.
; The updater of versions up to 1.1.6 handed us its PyInstaller variables
; (_PYI_*), which make the restarted exe look for python312.dll in the OLD,
; already deleted _MEI temp folder. Clear them so every old version updates
; cleanly to this one.
Function .onInstSuccess
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/RESTART" $R1
  ${IfNot} ${Errors}
    System::Call 'kernel32::SetEnvironmentVariable(t "_PYI_APPLICATION_HOME_DIR", p 0)'
    System::Call 'kernel32::SetEnvironmentVariable(t "_PYI_ARCHIVE_FILE", p 0)'
    System::Call 'kernel32::SetEnvironmentVariable(t "_PYI_PARENT_PROCESS_LEVEL", p 0)'
    System::Call 'kernel32::SetEnvironmentVariable(t "_PYI_SPLASH_IPC", p 0)'
    System::Call 'kernel32::SetEnvironmentVariable(t "_MEIPASS2", p 0)'
    System::Call 'kernel32::SetEnvironmentVariable(t "PYINSTALLER_RESET_ENVIRONMENT", t "1")'
    Exec '"$INSTDIR\${EXE}.exe"'
  ${EndIf}
FunctionEnd

; The app holds a mutex while its window is open (see app/main.py).
; Silent mode: no dialog, wait up to 30 s for the app to exit, else fail.
!macro WaitForAppClosed
  StrCpy $1 0
  retry:
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\${APP_ID}.SingleInstance") p.r0'
  ${If} $0 P<> 0
    System::Call 'kernel32::CloseHandle(p r0)'
    ${If} ${Silent}
      ${If} $1 < 30
        IntOp $1 $1 + 1
        Sleep 1000
        Goto retry
      ${EndIf}
      SetErrorLevel 2
      Abort
    ${EndIf}
    MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(RunningMsg)" IDOK retry
    Abort
  ${EndIf}
!macroend

Section "${APP_NAME}" SecMain
  SectionIn RO
  !insertmacro WaitForAppClosed
  SetOutPath "$INSTDIR"
  ; Upgrading this install (its exe is already here), not a fresh one?
  StrCpy $1 0
  ${If} ${FileExists} "$INSTDIR\${EXE}.exe"
    StrCpy $1 1
  ${EndIf}
  File "..\dist\${EXE}.exe"
  File "..\.env.example"
  ; Keep the user's settings and keys on upgrade.
  ${IfNot} ${FileExists} "$INSTDIR\.env"
    CopyFiles /SILENT "$INSTDIR\.env.example" "$INSTDIR\.env"
  ${EndIf}

  ; Releases before the edition split named the shortcuts after APP_ID; when
  ; the shown name differs (internal: "Doc2Excel Pro"), replace the old ones.
  !if "${APP_NAME}" != "${APP_ID}"
    ${If} $1 == 1
      Delete "$SMPROGRAMS\${APP_ID}.lnk"
      Delete "$DESKTOP\${APP_ID}.lnk"
    ${EndIf}
  !endif
  CreateShortCut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${EXE}.exe"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${EXE}.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXE}.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !insertmacro WaitForAppClosed
  Delete "$INSTDIR\${EXE}.exe"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\Uninstall.exe"
  ; .env (settings + API keys) is left in place; remove the folder only if empty.
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
