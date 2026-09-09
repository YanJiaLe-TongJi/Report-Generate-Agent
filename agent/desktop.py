"""Native window lifecycle and file-save bridge."""
import multiprocessing
import shutil
import threading
from pathlib import Path

class Bridge:
    def __init__(self, app):self._app=app;self._window=None
    def save_artifact(self,task_id,name):
        import webview
        try:
            source=self._app.artifact(task_id,name)
            result=self._window.create_file_dialog(webview.FileDialog.SAVE,save_filename=name)
            if result:
                dest=Path(result[0] if isinstance(result,(tuple,list)) else result)
                if dest.resolve()!=source.resolve():shutil.copy2(source,dest)
                return {'ok':True}
            return {'cancelled':True}
        except Exception:return {'error':'保存失败，请检查目标文件夹权限'}

def main():
    import webview
    from werkzeug.serving import make_server, WSGIRequestHandler
    from app import create_app
    class QuietHandler(WSGIRequestHandler):
        def log_request(self,*args,**kwargs):pass
    app=create_app();server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    bridge=Bridge(app)
    window=webview.create_window('物理报告生成器',f'http://127.0.0.1:{server.server_port}/?launch={app.access_token}',js_api=bridge,width=1180,height=850,min_size=(850,620))
    bridge._window=window
    try:webview.start(debug=False)
    finally:
        app.engine.stop();server.shutdown();server.server_close();worker.join(timeout=3)

if __name__=='__main__':
    multiprocessing.freeze_support()
    import sys
    if "--self-test" in sys.argv:
        from selftest import run
        run()
    else:
        main()
