"""
语音提醒模块
调用Windows系统语音播报
"""
import logging
import threading
import time

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False
    print("提示: 未安装 pyttsx3，将使用备用语音提醒方式")
    print("运行: pip install pyttsx3 来安装文字转语音库")

logger = logging.getLogger(__name__)


class VoiceAlert:
    """语音提醒器"""
    
    def __init__(self):
        self.enabled = True
        self.last_alert_time = {}
        self.cooldown = 60  # 同一 reminding的冷却时间
        self._engine = None
        self._lock = threading.Lock()
        
        if PYTTSX3_AVAILABLE:
            try:
                self._engine = pyttsx3.init()
                self._engine.setProperty('rate', 180)
                self._engine.setProperty('volume', 1.0)
            except Exception as e:
                logger.error(f"初始化语音引擎失败: {e}")
                self._engine = None
    
    def speak(self, signal):
        """
        播报买卖信号
        signal: dict 包含 'type' 和 'reason'
        """
        if not self.enabled:
            return
        
        # 检查冷却
        current_time = time.time()
        signal_type = signal.get('type', '')
        
        with self._lock:
            last_time = self.last_alert_time.get(signal_type, 0)
            if current_time - last_time < self.cooldown:
                return
            self.last_alert_time[signal_type] = current_time
        
        # 构建播报文本
        if signal_type == 'buy':
            text = f"买入信号！{signal.get('reason', '')}，当前价格 {signal.get('price', 0):.2f} 元"
        elif signal_type == 'sell':
            text = f"卖出信号！{signal.get('reason', '')}，当前价格 {signal.get('price', 0):.2f} 元"
        else:
            return
        
        # 使用pyttsx3播报
        if self._engine and PYTTSX3_AVAILABLE:
            try:
                self._engine.say(text)
                self._engine.runAndWait()
                logger.info(f"语音播报: {text}")
            except Exception as e:
                logger.error(f"语音播报失败: {e}")
                self._print_alert(text)
        else:
            self._print_alert(text)
    
    def _print_alert(self, text):
        """备用提醒方式：打印到控制台"""
        import winsound
        print(f"\n{'=' * 50}")
        print(f"【提醒】{text}")
        print(f"{'=' * 50}\n")
        
        # 播放提示音
        try:
            winsound.Beep(1000, 500)  # 频率1000Hz，持续500ms
        except:
            pass
    
    def set_enabled(self, enabled):
        """启用/禁用语音提醒"""
        self.enabled = enabled
    
    def test(self):
        """测试语音功能"""
        print("正在测试语音提醒功能...")
        self.speak({'type': 'buy', 'reason': '测试', 'price': 10.5})
        print("语音测试完成")
