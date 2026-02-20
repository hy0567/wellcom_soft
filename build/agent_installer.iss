; WellcomSOFT Agent 설치 스크립트 (Inno Setup)
; 빌드: ISCC.exe build/agent_installer.iss
; 입력: dist/WellcomAgent/ (PyInstaller onedir 결과)
; 출력: dist/WellcomAgent_Setup.exe

#define MyAppName "WellcomAgent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Wellcom"
#define MyAppExeName "WellcomAgent.exe"

[Setup]
AppId={{B8D4F6C2-1234-5E3A-9B2C-4F6A8E0D2C5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\WellcomAgent
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=WellcomAgent_Setup
SetupIconFile=wellcom.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 업그레이드(덮어쓰기) 허용
UsePreviousAppDir=yes
CloseApplications=force
RestartApplications=no

ShowLanguageDialog=no
DisableWelcomePage=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
korean.WelcomeLabel1=WellcomAgent 설치
korean.WelcomeLabel2=WellcomSOFT Agent를 컴퓨터에 설치합니다.%n%n이 프로그램은 관리 PC에서 원격 제어를 받기 위한 에이전트입니다.%n%n기존 버전이 있으면 자동으로 업그레이드됩니다.%n%n계속하려면 [다음]을 클릭하세요.
korean.FinishedHeadingLabel=WellcomAgent 설치 완료
korean.FinishedLabel=WellcomAgent가 성공적으로 설치되었습니다.

[Files]
; EXE + _internal (런타임)
Source: "..\dist\WellcomAgent\WellcomAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\WellcomAgent\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\logs"; Permissions: everyone-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; 시작프로그램에 등록
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 생성"; GroupDescription: "추가 옵션:"
Name: "startupicon"; Description: "Windows 시작 시 자동 실행"; GroupDescription: "추가 옵션:"; Flags: checkedonce

[Run]
; 설치 후 에이전트 실행
Filename: "{app}\{#MyAppExeName}"; Description: "WellcomAgent 실행"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 제거 전 프로세스 종료
Filename: "taskkill"; Parameters: "/f /im WellcomAgent.exe"; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// 설치 전: 실행 중인 에이전트 종료
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('taskkill', '/f /im WellcomAgent.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

// 제거 시 시작프로그램 레지스트리 정리
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // 레지스트리 시작프로그램 항목 제거
    RegDeleteValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'WellcomAgent');
  end;
end;
