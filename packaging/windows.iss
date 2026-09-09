[Setup]
AppName={#AppName}
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Windows-x64-unsigned
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppName}.exe
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebViewInstaller}"; DestDir: "{tmp}"; Flags: deleteafterinstall
[Icons]
Name: "{group}\物理报告生成器"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\物理报告生成器"; Filename: "{app}\{#AppName}.exe"
[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "配置 WebView2 运行时（可能需要网络）"; Flags: waituntilterminated
Filename: "{app}\{#AppName}.exe"; Description: "打开物理报告生成器"; Flags: postinstall nowait skipifsilent
