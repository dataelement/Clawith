import asyncio
from app.services.sandbox.config import SandboxConfig

config = {"allow_network": True}
fallback = SandboxConfig(allow_network=False)
result = SandboxConfig.from_dict(config, fallback)
print(f"Result 1: {result.allow_network}")

config2 = {"allow_network": "true"}
result2 = SandboxConfig.from_dict(config2, fallback)
print(f"Result 2: {result2.allow_network}")

config3 = {"allow_network": "1"}
result3 = SandboxConfig.from_dict(config3, fallback)
print(f"Result 3: {result3.allow_network}")

config4 = {}
result4 = SandboxConfig.from_dict(config4, fallback)
print(f"Result 4: {result4.allow_network}")
