"""Compatibility port for explicitly trusted development CLI/test fixtures."""

from jarvis.application import FRIEND_ID, GUEST_ID, Application, build_application

DevelopmentCognitiveRuntime = Application


def development_runtime(path, **kwargs):
    return build_application(path, **kwargs)


__all__ = ["FRIEND_ID", "GUEST_ID", "DevelopmentCognitiveRuntime", "development_runtime"]
