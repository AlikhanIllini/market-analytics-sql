# Convenience targets. Assumes Docker is running.
DATABASE_URL ?= postgresql://quant:quant@localhost:5432/market
export DATABASE_URL

db-up:        ## start postgres
	docker compose up -d

seed:         ## generate the offline sample dataset
	python data/generate_seed.py

load-seed:    ## load the offline sample dataset
	python data/load_data.py --seed

load-real:    ## pull real data from public APIs
	python data/load_data.py

dashboard:    ## launch the streamlit dashboard
	streamlit run dashboard/app.py

db-down:      ## stop postgres
	docker compose down
