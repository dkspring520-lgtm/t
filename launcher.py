"""
A股做T监控系统启动器
简化启动流程，自动检查依赖并启动服务
"""
import sys
import subprocess
import os

def check_dependencies():
    """检查并安装依赖"""
    required = ['flask', 'flask-cors', 'flask-socketio', 'requests']
    missing = []
    
    for package in required:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        print("正在安装...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing)
        print("依赖安装完成!")

def main():
    """主函数"""
    print("=" * 50)
    print("   A股做T监控系统")
    print("=" * 50)
    
    # 检查依赖
    check_dependencies()
    
    # 启动服务
    print("\n启动服务器...")
    print("访问地址: http://127.0.0.1:5000")
    print("按 Ctrl+C 停止\n")
    
    # 启动 Flask 应用
    from app import app, socketio
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

if __name__ == '__main__':
    main()
