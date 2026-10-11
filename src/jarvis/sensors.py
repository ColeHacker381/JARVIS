"""Simulated sources through the future adapter ingestion boundary. No hardware or recognition."""

from dataclasses import dataclass
from uuid import uuid4

from jarvis.core.world_records import ObservationInput, SensorPayload


@dataclass(frozen=True)
class FakeSensor:
    source_id: str
    entity_id: str
    location: str
    observation_type: str

    def observation(
        self,
        *,
        observed_at,
        value=True,
        confidence=0.8,
        source_available=True,
        observation_id=None,
        event_id=None,
        correlation_id=None,
        kind=None,
        entity_id=None,
    ):
        return ObservationInput(
            source_id=self.source_id,
            entity_id=entity_id or self.entity_id,
            location=self.location,
            observation_type=kind or self.observation_type,
            observed_at=observed_at,
            confidence=confidence,
            payload=SensorPayload(value=value),
            source_available=source_available,
            observation_id=observation_id or uuid4(),
            event_id=event_id or uuid4(),
            correlation_id=correlation_id or uuid4(),
        )

    def publish(self, ingestion, *, session_token, **values):
        return ingestion.ingest(self.observation(**values), session_token=session_token)

    def disconnect(self, ingestion, *, session_token, observed_at):
        return self.publish(
            ingestion,
            session_token=session_token,
            observed_at=observed_at,
            kind="availability",
            entity_id=self.source_id,
            value=False,
            confidence=1.0,
        )


class FakeCamera(FakeSensor):
    def __init__(self, source_id, entity_id, location):
        super().__init__(source_id, entity_id, location, "person_detected")


class FakeOccupancySensor(FakeSensor):
    def __init__(self, source_id, entity_id, location, *, motion=False):
        super().__init__(source_id, entity_id, location, "motion" if motion else "occupancy")


class FakeDeviceStatus(FakeSensor):
    def __init__(self, source_id, entity_id, location):
        super().__init__(source_id, entity_id, location, "availability")
