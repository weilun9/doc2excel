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
!define APP "Doc2Excel"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "..\dist\${APP}-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "..\assets\icon.ico"
UninstallIcon "..\assets\icon.ico"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" ""

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP}.exe"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

LangString RunningMsg ${LANG_SIMPCHINESE} "Doc2Excel 正在运行。请先关闭它的窗口，再按“确定”继续。"
LangString RunningMsg ${LANG_ENGLISH} "Doc2Excel is running. Close its window, then press OK to continue."

Function .onInit
  ${IfNot} ${Silent}
    !insertmacro MUI_LANGDLL_DISPLAY
  ${EndIf}
FunctionEnd

; /RESTART: the in-app updater closed Doc2Excel to install; open it again.
Function .onInstSuccess
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/RESTART" $R1
  ${IfNot} ${Errors}
    Exec '"$INSTDIR\${APP}.exe"'
  ${EndIf}
FunctionEnd

; The app holds a mutex while its window is open (see app/main.py).
; Silent mode: no dialog, wait up to 30 s for the app to exit, else fail.
!macro WaitForAppClosed
  StrCpy $1 0
  retry:
  System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, w "Local\Doc2Excel.SingleInstance") p.r0'
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

Section "Doc2Excel" SecMain
  SectionIn RO
  !insertmacro WaitForAppClosed
  SetOutPath "$INSTDIR"
  File "..\dist\${APP}.exe"
  File "..\.env.example"
  ; Keep the user's settings and keys on upgrade.
  ${IfNot} ${FileExists} "$INSTDIR\.env"
    CopyFiles /SILENT "$INSTDIR\.env.example" "$INSTDIR\.env"
  ${EndIf}

  CreateShortCut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${APP}.exe"
  CreateShortCut "$DESKTOP\${APP}.lnk" "$INSTDIR\${APP}.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP}.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !insertmacro WaitForAppClosed
  Delete "$INSTDIR\${APP}.exe"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\Uninstall.exe"
  ; .env (settings + API keys) is left in place; remove the folder only if empty.
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
