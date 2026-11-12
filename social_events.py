import asyncio
import os
import sys

# Import event bus locally or from fallback path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.expanduser("~/test"))
from event_bus import EventBus

class UserConnectedEvent:
    def __init__(self, user_id_a: int, user_id_b: int):
        self.user_id_a = user_id_a
        self.user_id_b = user_id_b

class SocialEventDispatcher:
    def __init__(self):
        self.bus = EventBus()
        self.delivered_notifications = []

        @self.bus.subscribe(UserConnectedEvent, priority=10)
        async def on_user_connected(event: UserConnectedEvent):
            self.delivered_notifications.append(f"Connection created between {event.user_id_a} and {event.user_id_b}")

    async def connect_users(self, a: int, b: int):
        await self.bus.publish(UserConnectedEvent(a, b))