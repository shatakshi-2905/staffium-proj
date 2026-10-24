-- 006_reference_data.sql
-- Reference data the application needs to run: the application state machine and the skill dictionary.

SET search_path = public, extensions;

INSERT INTO application_status_transitions (from_status, to_status, actor) VALUES
    -- Recruiter-driven pipeline moves
    ('APPLIED',     'SCREENING',   'RECRUITER'),
    ('APPLIED',     'SHORTLISTED', 'RECRUITER'),
    ('APPLIED',     'REJECTED',    'RECRUITER'),
    ('SCREENING',   'SHORTLISTED', 'RECRUITER'),
    ('SCREENING',   'REJECTED',    'RECRUITER'),
    ('SHORTLISTED', 'INTERVIEW',   'RECRUITER'),
    ('SHORTLISTED', 'REJECTED',    'RECRUITER'),
    ('INTERVIEW',   'REJECTED',    'RECRUITER'),
    ('OFFER',       'REJECTED',    'RECRUITER'),
    -- A candidate can withdraw from any open stage
    ('APPLIED',     'WITHDRAWN',   'APPLICANT'),
    ('SCREENING',   'WITHDRAWN',   'APPLICANT'),
    ('SHORTLISTED', 'WITHDRAWN',   'APPLICANT'),
    ('INTERVIEW',   'WITHDRAWN',   'APPLICANT'),
    ('OFFER',       'WITHDRAWN',   'APPLICANT')
ON CONFLICT DO NOTHING;
-- Not listed on purpose: -> OFFER (only by creating an offer), -> HIRED (only by the candidate
-- accepting it), and anything out of HIRED / REJECTED / WITHDRAWN (terminal states).

INSERT INTO skills (skill_name, category) VALUES
    ('Python', 'Language'), ('Java', 'Language'), ('JavaScript', 'Language'), ('TypeScript', 'Language'),
    ('C', 'Language'), ('C++', 'Language'), ('C#', 'Language'), ('Go', 'Language'), ('Rust', 'Language'),
    ('Kotlin', 'Language'), ('Swift', 'Language'), ('Ruby', 'Language'), ('PHP', 'Language'), ('Scala', 'Language'),
    ('R', 'Language'), ('Dart', 'Language'), ('Bash', 'Language'), ('MATLAB', 'Language'),
    ('SQL', 'Database'), ('PostgreSQL', 'Database'), ('MySQL', 'Database'), ('SQLite', 'Database'),
    ('MongoDB', 'Database'), ('Redis', 'Database'), ('Cassandra', 'Database'), ('Elasticsearch', 'Database'),
    ('DynamoDB', 'Database'), ('Oracle Database', 'Database'), ('SQL Server', 'Database'), ('Firebase', 'Database'),
    ('Supabase', 'Database'), ('pgvector', 'Database'), ('Neo4j', 'Database'),
    ('React', 'Frontend'), ('Next.js', 'Frontend'), ('Vue.js', 'Frontend'), ('Angular', 'Frontend'),
    ('Svelte', 'Frontend'), ('HTML', 'Frontend'), ('CSS', 'Frontend'), ('Tailwind CSS', 'Frontend'),
    ('Redux', 'Frontend'), ('React Native', 'Mobile'), ('Flutter', 'Mobile'), ('Android', 'Mobile'), ('iOS', 'Mobile'),
    ('Node.js', 'Backend'), ('Express.js', 'Backend'), ('NestJS', 'Backend'), ('Django', 'Backend'),
    ('Flask', 'Backend'), ('FastAPI', 'Backend'), ('Spring Boot', 'Backend'), ('ASP.NET', 'Backend'),
    ('Ruby on Rails', 'Backend'), ('Laravel', 'Backend'), ('GraphQL', 'Backend'), ('REST APIs', 'Backend'),
    ('gRPC', 'Backend'), ('Microservices', 'Backend'), ('Kafka', 'Backend'), ('RabbitMQ', 'Backend'),
    ('AWS', 'Cloud'), ('Azure', 'Cloud'), ('Google Cloud', 'Cloud'), ('AWS Lambda', 'Cloud'), ('Amazon S3', 'Cloud'),
    ('Amazon EC2', 'Cloud'), ('Amazon Bedrock', 'Cloud'), ('Serverless', 'Cloud'),
    ('Docker', 'DevOps'), ('Kubernetes', 'DevOps'), ('Terraform', 'DevOps'), ('Ansible', 'DevOps'),
    ('Jenkins', 'DevOps'), ('GitHub Actions', 'DevOps'), ('CI/CD', 'DevOps'), ('Linux', 'DevOps'),
    ('Nginx', 'DevOps'), ('Prometheus', 'DevOps'), ('Grafana', 'DevOps'), ('Git', 'DevOps'),
    ('Machine Learning', 'AI/ML'), ('Deep Learning', 'AI/ML'), ('NLP', 'AI/ML'), ('Computer Vision', 'AI/ML'),
    ('TensorFlow', 'AI/ML'), ('PyTorch', 'AI/ML'), ('scikit-learn', 'AI/ML'), ('Keras', 'AI/ML'),
    ('Hugging Face', 'AI/ML'), ('LLMs', 'AI/ML'), ('LangChain', 'AI/ML'), ('Prompt Engineering', 'AI/ML'),
    ('RAG', 'AI/ML'), ('MLOps', 'AI/ML'), ('Reinforcement Learning', 'AI/ML'), ('OpenCV', 'AI/ML'),
    ('Pandas', 'Data'), ('NumPy', 'Data'), ('Apache Spark', 'Data'), ('Hadoop', 'Data'), ('Airflow', 'Data'),
    ('dbt', 'Data'), ('Snowflake', 'Data'), ('BigQuery', 'Data'), ('Data Analysis', 'Data'),
    ('Data Visualization', 'Data'), ('Power BI', 'Data'), ('Tableau', 'Data'), ('Excel', 'Data'),
    ('Statistics', 'Data'), ('ETL', 'Data'),
    ('System Design', 'Engineering'), ('Data Structures', 'Engineering'), ('Algorithms', 'Engineering'),
    ('Object-Oriented Programming', 'Engineering'), ('Unit Testing', 'Engineering'), ('Pytest', 'Engineering'),
    ('Jest', 'Engineering'), ('Selenium', 'Engineering'), ('Cybersecurity', 'Engineering'),
    ('Networking', 'Engineering'), ('Agile', 'Process'), ('Scrum', 'Process'), ('Jira', 'Process'),
    ('Product Management', 'Business'), ('Project Management', 'Business'), ('Figma', 'Design'),
    ('UI Design', 'Design'), ('UX Research', 'Design'), ('Communication', 'Soft skill'),
    ('Leadership', 'Soft skill'), ('Problem Solving', 'Soft skill'), ('Technical Writing', 'Soft skill')
ON CONFLICT DO NOTHING;
