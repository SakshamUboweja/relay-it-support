import os
import uvicorn
from .config import validate_environment


def main():
    validate_environment(cloud=bool(os.getenv("RAILWAY_ENVIRONMENT_ID")))
    uvicorn.run(
        "relay.api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "3000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
