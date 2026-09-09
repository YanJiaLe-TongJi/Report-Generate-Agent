"""Install a relocatable, private TeX Live runtime; never modifies system PATH."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

PACKAGES='xetex ctex fandol geometry graphics pgf eso-pic titlesec setspace amsmath amsfonts booktabs tools multirow float caption enumitem siunitx hyperref unicode-math'.split()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',required=True);parser.add_argument('--repository',default='https://mirror.ctan.org/systems/texlive/tlnet');args=parser.parse_args()
    dest=Path(args.destination).resolve();dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp);archive=temp/'installer.tar.gz'
        subprocess.run(['curl','-fL','--retry','3',args.repository+'/install-tl-unx.tar.gz','-o',str(archive)],check=True)
        with tarfile.open(archive) as tf:tf.extractall(temp,filter='data')
        installer=next(temp.glob('install-tl-*/install-tl'))
        profile=temp/'tex.profile';profile.write_text(f'selected_scheme scheme-minimal\nTEXDIR {dest.as_posix()}\nTEXMFCONFIG {dest.as_posix()}/texmf-config\nTEXMFVAR {dest.as_posix()}/texmf-var\nTEXMFSYSCONFIG {dest.as_posix()}/texmf-config\nTEXMFSYSVAR {dest.as_posix()}/texmf-var\nTEXMFLOCAL {dest.as_posix()}/texmf-local\nTEXMFHOME {dest.as_posix()}/texmf-home\noption_doc 0\noption_src 0\noption_path 0\noption_adjustrepo 0\ninstopt_portable 1\n',encoding='utf-8')
        subprocess.run(['perl',str(installer),'-profile',str(profile),'-repository',args.repository,'-no-interaction'],check=True)
    tlmgr=next(dest.glob('bin/*/tlmgr.bat' if os.name=='nt' else 'bin/*/tlmgr'))
    subprocess.run([str(tlmgr),'--repository',args.repository,'install',*PACKAGES],check=True)
    (dest/'DESKTOP-RUNTIME.txt').write_text('Private TeX Live runtime. Packages: '+' '.join(PACKAGES)+'\nRepository: '+args.repository+'\nLicenses: texmf-dist/doc and LICENSE.TL / LICENSE.CTAN.\n',encoding='utf-8')
if __name__=='__main__':main()
