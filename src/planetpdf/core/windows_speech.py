"""View ▸ Read Out Loud (Acrobat; Windows only; backlog Phase D, built 2026-10-02): the
document's text spoken by the Windows voices (SAPI, through comtypes), without blocking
the window: speaking runs asynchronously; pause, resume and stop come back at once.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("Read Out Loud is only available on Windows.")

SVSF_ASYNC = 1
SVSF_PURGE = 2  # purge what's queued (stop)
SRSE_DONE = 1  # SpeechRunState.SRSEDone


class Speaker:
    def __init__(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
        self.paused = False

    @property
    def voices(self) -> list[str]:
        tokens = self._voice.GetVoices()
        return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]

    def voice_labels(self) -> list[tuple[str, str]]:
        """(name, name with "male" / "female" when Windows says) for each installed voice."""
        tokens = self._voice.GetVoices()
        out = []
        for i in range(tokens.Count):
            token = tokens.Item(i)
            name = token.GetDescription()
            try:
                gender = str(token.GetAttribute("Gender") or "").lower()
            except Exception:
                gender = ""
            out.append((name, f"{name} ({gender})" if gender in ("male", "female") else name))
        return out

    def set_voice(self, name: str) -> None:
        tokens = self._voice.GetVoices()
        for i in range(tokens.Count):
            if tokens.Item(i).GetDescription() == name:
                self._voice.Voice = tokens.Item(i)
                return

    @property
    def rate(self) -> int:
        return int(self._voice.Rate)

    @rate.setter
    def rate(self, value: int) -> None:
        self._voice.Rate = max(-10, min(10, int(value)))

    @property
    def volume(self) -> int:
        return int(self._voice.Volume)

    @volume.setter
    def volume(self, value: int) -> None:
        self._voice.Volume = max(0, min(100, int(value)))

    def speak(self, text: str) -> None:
        """Start reading ``text`` (replaces anything being read)."""
        self.stop()
        if text.strip():
            self._voice.Speak(text, SVSF_ASYNC | SVSF_PURGE)

    @property
    def speaking(self) -> bool:
        try:
            return int(self._voice.Status.RunningState) != SRSE_DONE
        except Exception:
            return False

    def pause(self) -> None:
        if not self.paused:
            self._voice.Pause()
            self.paused = True

    def resume(self) -> None:
        if self.paused:
            self._voice.Resume()
            self.paused = False

    def stop(self) -> None:
        if self.paused:
            self.resume()
        self._voice.Speak("", SVSF_ASYNC | SVSF_PURGE)

    def wait(self, ms: int) -> bool:
        """Block up to ``ms`` milliseconds; True if it finished (tests)."""
        return bool(self._voice.WaitUntilDone(ms))
