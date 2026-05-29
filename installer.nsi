; installer.nsi
; NSIS installer script for Canvas Archive (Windows)

!define APP_NAME "Canvas Archive"
!define APP_VERSION "1.0.0"
!define APP_PUBLISHER "Canvas Archive"
!define APP_URL "https://github.com/YOURUSERNAME/canvas-archive"
!define APP_EXE "Canvas Archive.exe"
!define INSTALL_DIR "$PROGRAMFILES64\Canvas Archive"

Name "${APP_NAME}"
OutFile "CanvasArchiveSetup.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKLM "Software\Canvas Archive" ""
RequestExecutionLevel admin
SetCompressor /SOLID lzma

; Modern UI
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_HEADERIMAGE
!define MUI_BGCOLOR "FFFFFF"
!define MUI_WELCOMEFINISHPAGE_BITMAP_NOSTRETCH

!define MUI_WELCOMEPAGE_TITLE "Welcome to Canvas Archive"
!define MUI_WELCOMEPAGE_TEXT "Canvas Archive saves all your course materials from Canvas before you lose access.$\r$\n$\r$\nThis will install Canvas Archive on your computer.$\r$\n$\r$\nClick Next to continue."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "Canvas Archive is installed!"
!define MUI_FINISHPAGE_TEXT "Canvas Archive has been installed.$\r$\n$\r$\nClick Finish to launch it now."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Canvas Archive now"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Main" SecMain
    SetOutPath "$INSTDIR"
    
    ; Copy all files from PyInstaller output
    File /r "dist\Canvas Archive\*.*"
    
    ; Write registry keys
    WriteRegStr HKLM "Software\Canvas Archive" "" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "URLInfoAbout" "${APP_URL}"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive" \
        "NoRepair" 1

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Desktop shortcut
    CreateShortcut "$DESKTOP\Canvas Archive.lnk" \
        "$INSTDIR\${APP_EXE}" "" \
        "$INSTDIR\${APP_EXE}" 0

    ; Start Menu shortcut
    CreateDirectory "$SMPROGRAMS\Canvas Archive"
    CreateShortcut "$SMPROGRAMS\Canvas Archive\Canvas Archive.lnk" \
        "$INSTDIR\${APP_EXE}"
    CreateShortcut "$SMPROGRAMS\Canvas Archive\Uninstall.lnk" \
        "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
    RMDir /r "$INSTDIR"
    Delete "$DESKTOP\Canvas Archive.lnk"
    RMDir /r "$SMPROGRAMS\Canvas Archive"
    DeleteRegKey HKLM "Software\Canvas Archive"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CanvasArchive"
SectionEnd