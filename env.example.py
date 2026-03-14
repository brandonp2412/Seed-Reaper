# Copy this file to env.py and fill in your own values.
# env.py is gitignored — never commit real credentials.

# Transmission
TRANSMISSION_HOST     = "192.168.1.x"
TRANSMISSION_PORT     = 9091
TRANSMISSION_USERNAME = "transmission"
TRANSMISSION_PASSWORD = "your-transmission-password"

# Sonarr — set to "" to disable Sonarr integration
SONARR_APIKEY = ""

# TMDB — https://www.themoviedb.org/settings/api
# Set to "" to disable TMDB classification fallback
TMDB_APIKEY = ""

# Reaper thresholds
MAX_RATIO    = 2.0   # remove torrent when upload/download ratio reaches this
MAX_AGE_DAYS = 30    # remove torrent after seeding this many days
