"""Install a relocatable, private TeX Live runtime; never modifies system PATH."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

PACKAGES='xetex ctex fandol geometry graphics pgf eso-pic titlesec setspace amsmath amsfonts booktabs tools multirow float caption enumitem siunitx hyperref unicode-math'.split()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True);parser.add_argument('--repository',default='https://mirror.ctan.org/systems/texlive/tlnet');args=parser.parse_args()
    # Resolve CTAN redirect once: two mirror choices can have different revisions.
    effective=subprocess.check_output(['curl','-fsSL','-o',os.devnull,'-w','%{url_effective}',args.repository+'/tlpkg/texlive.tlpdb'],text=True)
    args.repository=effective.rsplit('/tlpkg/',1)[0]
    dest=Path(args.destination).resolve();dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp);archive=temp/('installer.zip' if os.name=='nt' else 'installer.tar.gz')
        archive_name='install-tl.zip' if os.name=='nt' else 'install-tl-unx.tar.gz'
        subprocess.run(['curl','-fL','--retry','3',args.repository+'/'+archive_name,'-o',str(archive)],check=True)
        if os.name=='nt':
            with zipfile.ZipFile(archive) as z:z.extractall(temp)
        else:
            with tarfile.open(archive) as tf:tf.extractall(temp,filter='data')
        installer=next(temp.rglob('install-tl'))
        perl=str(next(temp.rglob('perl.exe'))) if os.name=='nt' else 'perl'
        profile=temp/'tex.profile';profile.write_text(f'selected_scheme scheme-minimal\nTEXDIR {dest.as_posix()}\nTEXMFCONFIG {dest.as_posix()}/texmf-config\nTEXMFVAR {dest.as_posix()}/texmf-var\nTEXMFSYSCONFIG {dest.as_posix()}/texmf-config\nTEXMFSYSVAR {dest.as_posix()}/texmf-var\nTEXMFLOCAL {dest.as_posix()}/texmf-local\nTEXMFHOME {dest.as_posix()}/texmf-home\noption_doc 0\noption_src 0\noption_path 0\noption_adjustrepo 0\ninstopt_portable 1\n',encoding='utf-8')
        subprocess.run([perl,str(installer),'-profile',str(profile),'-repository',args.repository,'-no-interaction'],check=True)
    tlmgr=next(dest.glob('bin/*/tlmgr.bat' if os.name=='nt' else 'bin/*/tlmgr'))
    subprocess.run([str(tlmgr),'--repository',args.repository,'install',*PACKAGES],check=True)
    (dest/'DESKTOP-RUNTIME.txt').write_text('Private TeX Live runtime. Packages: '+' '.join(PACKAGES)+'\nRepository: '+args.repository+'\nLicenses: texmf-dist/doc and LICENSE.TL / LICENSE.CTAN.\n',encoding='utf-8')
if __name__=='__main__':main()
