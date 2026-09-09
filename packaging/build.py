"""Run on each target OS/architecture. Produces an unsigned test installer."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--edition',choices=['word','full'],required=True);parser.add_argument('--tex-runtime');args=parser.parse_args()
    if args.edition=='full' and (not args.tex_runtime or not Path(args.tex_runtime).is_dir()):parser.error('Full edition requires --tex-runtime from prepare_tex.py')
    name='PhysicsReport-'+args.edition;dist=ROOT/'dist'/args.edition
    with tempfile.TemporaryDirectory() as tmp:
        stage=Path(tmp)/'agent';shutil.copytree(ROOT/'agent',stage,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT/'LICENSE',stage/'static/LICENSE.txt')
        (stage/'build_info.py').write_text('EDITION = '+repr(args.edition)+'\n')
        command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--name',name,'--distpath',str(dist),'--workpath',str(Path(tmp)/'build'),'--specpath',tmp,'--paths',str(stage),'--add-data',str(stage/'templates')+os.pathsep+'templates','--add-data',str(stage/'static')+os.pathsep+'static','--hidden-import','build_info','--collect-all','docx','--collect-all','keyring','--collect-all','latex2mathml','--collect-all','webview','--exclude-module','PyQt5','--exclude-module','PyQt6','--exclude-module','PySide2','--exclude-module','PySide6',str(stage/'desktop.py')]
        subprocess.run(command,check=True)
    runtime=Path(args.tex_runtime).resolve() if args.tex_runtime else None
    if sys.platform=='darwin':
        app=dist/(name+'.app')
        if runtime:
            shutil.copytree(runtime,app/'Contents/Resources/texlive',symlinks=True)
            # The frozen Python module lives under Frameworks.
            (app/'Contents/Frameworks/texlive').symlink_to('../Resources/texlive')
        subprocess.run([str(app/'Contents/MacOS'/name),'--self-test'],check=True,env={**os.environ,'REPORT_APP_DATA':str(ROOT/'work'/('smoke-'+args.edition))})
        subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
        volume=dist/'dmg-content';volume.mkdir(exist_ok=True)
        shutil.copytree(app,volume/app.name,symlinks=True,dirs_exist_ok=True)
        if not (volume/'Applications').exists():(volume/'Applications').symlink_to('/Applications')
        output=dist/(name+'-macOS-'+platform.machine()+'-unsigned.dmg')
        subprocess.run(['hdiutil','create','-volname',name,'-srcfolder',str(volume),'-ov','-format','UDZO',str(output)],check=True)
        shutil.rmtree(volume)
    elif sys.platform=='win32':
        if runtime:shutil.copytree(runtime,dist/name/'_internal/texlive',symlinks=False)
        subprocess.run([str(dist/name/(name+'.exe')),'--self-test'],check=True,env={**os.environ,'REPORT_APP_DATA':str(ROOT/'work'/('smoke-'+args.edition))})
        bootstrap=dist/'MicrosoftEdgeWebview2Setup.exe'
        subprocess.run(['curl','-fL','https://go.microsoft.com/fwlink/p/?LinkId=2124703','-o',str(bootstrap)],check=True)
        compiler=shutil.which('iscc') or r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
        subprocess.run([compiler,f'/DAppName={name}',f'/DSourceDir={dist/name}',f'/DOutputDir={dist}',f'/DWebViewInstaller={bootstrap}',str(ROOT/'packaging/windows.iss')],check=True)
        output=dist/(name+'-Windows-x64-unsigned.exe')
    else:raise SystemExit('Only Windows and macOS are supported')
    output.with_suffix(output.suffix+'.sha256').write_text(hashlib.file_digest(output.open('rb'),'sha256').hexdigest()+'  '+output.name+'\n')
    print(output)
if __name__=='__main__':main()
