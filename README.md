# Dashboard Intervals Test

Test Intervals.icu API integration with Streamlit



# dashboard-intervals-test/
├── app.py                    # Main app
├── config.py                 # Configuration
├── requirements.txt          # Dependencies
├── .gitignore
├── README.md
└── utils/
    ├── __init__.py
    └── intervals_client.py   # API client


## Endpoints Used

- `GET /athlete/activities` — List all activities
- `GET /athlete/activities/{id}` — Activity details
- `GET /athlete/activities/{id}/streams` — Time series data
- `GET /athlete` — Athlete profile

