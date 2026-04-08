"""目标人物识别结果聚合与多帧投票。

核心类 PersonHitWindow 用于判断"是否在时间窗口内稳定识别到指定目标人物"。
单帧识别不直接触发，必须在窗口内累计达到阈值才视为命中。

设计决策：
- 只有 identified_name == target_name 的帧才计为命中
- 其他家庭成员、未识别、不确定 —— 均不计为命中
- 命中状态不会被自动消费；调用方需显式调用 consume() 才清空
- 这样可以让"非告警时段的命中"不被白白丢掉
"""

from collections import deque


class PersonHitWindow:
    """目标人物多帧投票窗口。

    使用方式：
        1. record(name, time)  — 记录一帧识别结果
        2. is_hit()            — 查询是否达到阈值
        3. consume()           — 确认触发后清空状态，避免重复触发
    """

    def __init__(
        self,
        target_name: str,
        frame_threshold: int,
        window_seconds: float,
    ) -> None:
        self._target = target_name
        self._threshold = frame_threshold
        self._window = window_seconds
        self._hits: deque[float] = deque()

    @property
    def target_name(self) -> str:
        return self._target

    def _purge_old(self, now: float) -> None:
        while self._hits and now - self._hits[0] > self._window:
            self._hits.popleft()

    def record(self, identified_name: str | None, event_time: float) -> None:
        """记录一帧识别结果。不消费命中状态。"""
        self._purge_old(event_time)
        if identified_name == self._target:
            self._hits.append(event_time)

    def is_hit(self) -> bool:
        """查询当前窗口内命中次数是否达到阈值。不改变内部状态。"""
        return len(self._hits) >= self._threshold

    def consume(self) -> None:
        """确认命中已被消费（触发告警后调用），清空累积状态。"""
        self._hits.clear()
