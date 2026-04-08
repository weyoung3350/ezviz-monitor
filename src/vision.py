"""目标人物识别结果聚合与多帧投票。

核心类 PersonHitWindow 用于判断"是否在时间窗口内稳定识别到指定目标人物"。
单帧识别不直接触发，必须在窗口内累计达到阈值才视为命中。

设计决策：
- 只有 identified_name == target_name 的帧才计为命中
- 其他家庭成员、未识别、不确定 —— 均不计为命中
- 命中达到阈值后自动重置，避免同一波连续帧反复触发
"""

from collections import deque


class PersonHitWindow:
    """目标人物多帧投票窗口。

    在 window_seconds 时间窗口内，如果有 >= frame_threshold 帧
    识别为 target_name，则视为稳定命中，返回 True。

    命中后内部状态自动重置，下一次命中需重新累积。
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

    def record(self, identified_name: str | None, event_time: float) -> bool:
        """记录一帧识别结果。

        Args:
            identified_name: 本帧识别出的人物名称。
                - 等于 target_name → 计为一次命中
                - 其他名称或 None → 不计为命中
            event_time: 单调递增的时间戳（秒）

        Returns:
            True 表示在时间窗口内命中次数达到阈值（稳定识别到目标人物）。
        """
        self._purge_old(event_time)

        if identified_name == self._target:
            self._hits.append(event_time)

        if len(self._hits) >= self._threshold:
            self._hits.clear()
            return True

        return False
