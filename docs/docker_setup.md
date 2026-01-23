### Docker has been set up with python13
package requirements are in ./requirements_python13.txt

##### Dev files
Dockerfile.dev

docker-compose.dev.yml

#### Set up
- ensure you have setup postgresql database locally
- in your .env add `DATABASE_URI=postgresql+psycopg2://admin:admin@host.docker.internal:5432/mhsdb` (change admin:admin to your user and password, then mhsdb to your db name)(ensure it's `_URI` and not `_URL`)

- start docker daemon
- run  `docker compose -f docker-compose.dev.yml up -d --build`

#### admin credentials seeding
- If you had not done the admin credentials seeding locally,
 - run ` docker exec -it farajamh-web sh` to enter into the container's shell
 - run `python insert_admin_direct_to_db_in_container.py` to seed admin data to db
 - Open the localhost:5000 to open the system
 login credetials (username: admin@gmail.com password: Admin123!)

