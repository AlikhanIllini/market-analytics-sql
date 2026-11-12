# Convenience targets.
# The scripts find the database themselves: DATABASE_URL env var, then
# .streamlit/secrets.toml, then the local Docker database.

db-up:        ## start postgres
	docker compose up -d

seed:         ## refresh the bundled offline snapshot from live data
	python data/load_data.py --write-seed

load-seed:    ## load the offline sample dataset
	python data/load_data.py --seed

load-real:    ## pull real data from public APIs
	python data/load_data.py

dashboard:    ## launch the streamlit dashboard
	streamlit run dashboard/app.py

preview:      ## re-render dashboard/preview.png from the loaded data
	python dashboard/make_preview.py

db-down:      ## stop postgres
	docker compose down
