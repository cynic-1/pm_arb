"""
日志配置模块
提供统一的日志配置和自定义 print 函数替换
"""

import os
import logging
import builtins as _builtins
from datetime import datetime
from typing import Optional


def setup_logger(log_dir: str = "logs", log_pointer_env: Optional[str] = None) -> None:
    """
    配置日志系统，并将 print 替换为基于 logger 的函数

    Args:
        log_dir: 日志文件目录
        log_pointer_env: 指向当前日志文件的指针文件路径（环境变量）
    """
    # 创建日志目录
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass

    # 生成时间戳和日志文件名
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.abspath(os.path.join(log_dir, f"arbitrage_{ts}.log"))

    # 移除现有的处理器
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)

    # 创建格式化器 - 使用毫秒级时间戳
    fmt = logging.Formatter(
        '%(asctime)s.%(msecs)03d %(filename)s:%(lineno)d %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 文件处理器
    fh = logging.FileHandler(logfile, encoding='utf-8')
    fh.setFormatter(fmt)

    # 流处理器 (控制台输出)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    # 配置根 logger
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(fh)
    logging.root.addHandler(sh)

    # 写入指针文件
    if log_pointer_env:
        pointer_file = os.path.abspath(log_pointer_env)
    else:
        pointer_file = os.path.abspath(os.path.join(log_dir, "CURRENT_LOG"))

    try:
        with open(pointer_file, "w", encoding="utf-8") as pf:
            pf.write(logfile)
    except Exception:
        pass

    # 替换全局 print 函数
    _replace_print_with_logger()

    # 输出日志文件位置
    logger = logging.getLogger(__name__)
    logger.info(f"日志系统已初始化: {logfile}")


def _replace_print_with_logger() -> None:
    """将内置的 print 函数替换为使用 logger 的版本"""
    _logger = logging.getLogger("print_replacement")

    def _print(*args, sep=' ', end='\n', file=None, flush=False, level=logging.INFO):
        """
        替换的 print 函数，使用 logger 记录

        Args:
            *args: 要打印的参数
            sep: 分隔符
            end: 结束符 (未使用，保持兼容性)
            file: 文件对象 (未使用，保持兼容性)
            flush: 是否刷新 (未使用，保持兼容性)
            level: 日志级别
        """
        # 构建消息
        try:
            msg = sep.join(str(a) for a in args)
        except Exception:
            # 回退：如果对象无法正常转换
            msg = ' '.join([repr(a) for a in args])

        # 使用 stacklevel 显示原始调用者文件/行号
        # Wrapper 增加一个额外的帧，所以使用 stacklevel=3 指向调用者
        try:
            _logger.log(level, msg, stacklevel=3)
        except TypeError:
            # 旧版 Python 不支持 stacklevel：手动包含调用者信息
            try:
                import inspect
                frame = inspect.currentframe()
                if frame is not None:
                    caller = frame.f_back.f_back
                    if caller is not None:
                        info = f"{os.path.basename(caller.f_code.co_filename)}:{caller.f_lineno} "
                        _logger.log(level, info + msg)
                        return
            except Exception:
                pass
            _logger.log(level, msg)

    # 覆盖内置 print
    _builtins.print = _print
