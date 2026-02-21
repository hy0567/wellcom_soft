; WellcomSOFT 설치 스크립트 (Inno Setup)
; 빌드: ISCC.exe build/installer.iss
; 입력: dist/WellcomSOFT/ (PyInstaller onedir 결과)
; 출력: dist/WellcomSOFT_Setup.exe

#define MyAppName "WellcomSOFT"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "Wellcom"
#define MyAppExeName "WellcomSOFT.exe"

[Setup]
AppId={{A7C3E5B1-9876-4D2F-8A1B-3E5F7D9C1B4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\WellcomSOFT
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=WellcomSOFT_Setup
SetupIconFile=wellcom.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ★ 업그레이드(덮어쓰기) 허용
UsePreviousAppDir=yes
CloseApplications=force
RestartApplications=no

; 버전 표시 제거
ShowLanguageDialog=no
DisableWelcomePage=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
korean.WelcomeLabel1=WellcomSOFT 설치
korean.WelcomeLabel2=WellcomSOFT를 컴퓨터에 설치합니다.%n%n기존 버전이 있으면 자동으로 업그레이드됩니다.%n%n계속하려면 [다음]을 클릭하세요.
korean.FinishedHeadingLabel=WellcomSOFT 설치 완료
korean.FinishedLabel=WellcomSOFT가 성공적으로 설치되었습니다.

; 업데이트 시 기존 data/ 보존
[Files]
; EXE + _internal (런타임) — 항상 덮어쓰기
Source: "..\dist\WellcomSOFT\WellcomSOFT.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\WellcomSOFT\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; app/ 코드 - .pyc 파일 — ★ 항상 덮어쓰기 (업그레이드 시에도 최신 코드 반영)
Source: "..\dist\WellcomSOFT\_internal\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

; data/ 빈 폴더 생성 (기존 데이터 보존)
[Dirs]
Name: "{app}\data"; Permissions: everyone-full
Name: "{app}\logs"; Permissions: everyone-full
Name: "{app}\backup"; Permissions: everyone-full
Name: "{app}\app"; Permissions: everyone-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 생성"; GroupDescription: "추가 옵션:"

[Run]
; WellcomSOFT 실행
Filename: "{app}\{#MyAppExeName}"; Description: "WellcomSOFT 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 업데이트로 생성된 파일 정리 (data/는 보존)
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\backup"
Type: filesandordirs; Name: "{app}\temp"

[Code]
// 설치 전: 실행 중인 WellcomSOFT 종료 + 기존 버전 감지
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // taskkill로 실행 중인 프로세스 종료
  Exec('taskkill', '/f /im WellcomSOFT.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // 잠시 대기 (파일 잠금 해제)
  Sleep(500);
end;

// 이전 버전 감지 시 안내
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
end;

// 제거 시 data/ 보존 안내
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if DirExists(ExpandConstant('{app}\data')) then
    begin
      if MsgBox('사용자 데이터(data/ 폴더)를 삭제하시겠습니까?' + #13#10 +
                '삭제하면 장치 설정과 데이터가 모두 사라집니다.',
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(ExpandConstant('{app}\data'), True, True, True);
        DelTree(ExpandConstant('{app}'), True, True, True);
      end;
    end;
  end;
end;
