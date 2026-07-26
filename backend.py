from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class ReportHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # 读取上报请求体
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length).decode('utf-8')
        
        # 格式化打印收到的数据
        print("=" * 60)
        print("收到新的上报数据：")
        try:
            data = json.loads(body)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except:
            print(body)
        print("=" * 60 + "\n")
        
        # 返回200成功
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    
    # 禁用默认访问日志，避免刷屏
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(('127.0.0.1', 54188), ReportHandler)
    print("✅ 后台接收服务已启动")
    print("📍 监听地址：127.0.0.1:54188")
    print("📡 等待接收机器人上报数据...\n")
    server.serve_forever()