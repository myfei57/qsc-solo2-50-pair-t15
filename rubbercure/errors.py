"""平台统一异常类型。

硫化现场必须区分几类失败：顺序被破坏属于工程接线或逻辑错误，必须在调试
阶段就暴露；联锁条件不满足是运行期可恢复的，设备状态一旦满足就能继续；
在制配额、排队容量和机位占用要分别报因，模具登记冲突与持久化失败则要
单独归类，便于按类型复盘。审计日志按异常类型分别落盘，复盘时才能一眼
分清"顺序错"还是"条件没到"。
"""

from __future__ import annotations


class RubberCureError(Exception):
    """所有平台异常的基类，携带出错的组件名。"""

    def __init__(self, message: str, *, component: str = "rubbercure") -> None:
        super().__init__(message)
        self.message = message
        self.component = component

    def as_record(self) -> dict:
        """转成审计日志可以直接落盘的一条记录。"""

        return {
            "type": type(self).__name__,
            "component": self.component,
            "message": self.message,
        }


class SequenceError(RubberCureError):
    """硫化、开模、调度或停运顺序被破坏。"""


class InterlockError(RubberCureError):
    """联锁条件不满足，动作被拒绝。"""


class CapacityError(RubberCureError):
    """在制或队列容量不足，任务被拒绝入队。"""


class QueueCapacityError(CapacityError):
    """待调度队列的存储容量已满。"""


class NoIdlePressError(CapacityError):
    """所有硫化机位都被占用，任务无法立即派机。"""


class WipLimitError(CapacityError):
    """车间在制模具数已达配额上限。"""


class StorageError(RubberCureError):
    """持久化读写失败。"""


class ConfigError(RubberCureError):
    """配置缺失或取值非法。"""


class NamespaceError(RubberCureError):
    """命名空间重复注册或解析失败。"""


class MoldError(RubberCureError):
    """模具登记冲突、映射缺失或代际过期。"""


class CureError(RubberCureError):
    """硫化计时状态非法。"""
