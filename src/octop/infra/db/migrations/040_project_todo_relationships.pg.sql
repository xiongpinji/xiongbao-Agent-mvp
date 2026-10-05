CREATE TABLE IF NOT EXISTS project_todo_children (
project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
parent_todo_id TEXT NOT NULL, child_todo_id TEXT NOT NULL,
created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at BIGINT NOT NULL,
PRIMARY KEY(project_id,parent_todo_id,child_todo_id), UNIQUE(project_id,child_todo_id),
CHECK(parent_todo_id<>child_todo_id),
FOREIGN KEY(project_id,parent_todo_id) REFERENCES project_todos(project_id,todo_id) ON DELETE CASCADE,
FOREIGN KEY(project_id,child_todo_id) REFERENCES project_todos(project_id,todo_id) ON DELETE CASCADE
);
-- D2_STATEMENT_BOUNDARY
CREATE TABLE IF NOT EXISTS project_todo_children_state (
project_id TEXT NOT NULL, todo_id TEXT NOT NULL, revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991), updated_at BIGINT NOT NULL,
PRIMARY KEY(project_id,todo_id), FOREIGN KEY(project_id,todo_id) REFERENCES project_todos(project_id,todo_id) ON DELETE CASCADE
);
-- D2_STATEMENT_BOUNDARY
CREATE TABLE IF NOT EXISTS project_plan_hierarchy_state (
project_id TEXT NOT NULL PRIMARY KEY REFERENCES project_spaces(project_id) ON DELETE CASCADE,
revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991), updated_at BIGINT NOT NULL
);
-- D2_STATEMENT_BOUNDARY
CREATE TABLE IF NOT EXISTS project_todo_child_create_requests (
project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
parent_todo_id TEXT NOT NULL, actor_user_id INTEGER NOT NULL CHECK(actor_user_id>0),
client_request_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
child_todo_id TEXT NOT NULL, created_at BIGINT NOT NULL,
result_state TEXT NOT NULL CHECK(result_state IN ('recorded','invalidated')),
invalidated_at BIGINT,
PRIMARY KEY(project_id,parent_todo_id,actor_user_id,client_request_id),
CHECK((result_state='recorded' AND invalidated_at IS NULL) OR (result_state='invalidated' AND invalidated_at IS NOT NULL)),
CHECK(length(request_fingerprint)=64 AND request_fingerprint ~ '^[0-9a-f]{64}$'),
CHECK(client_request_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
);
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_todo_init() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN INSERT INTO project_todo_children_state(project_id,todo_id,revision,updated_at) VALUES(NEW.project_id,NEW.todo_id,1,NEW.created_at); RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_todo_init AFTER INSERT ON project_todos FOR EACH ROW EXECUTE FUNCTION d2_todo_init();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_project_init() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN INSERT INTO project_plan_hierarchy_state(project_id,revision,updated_at) VALUES(NEW.project_id,1,NEW.created_at); RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_project_init AFTER INSERT ON project_spaces FOR EACH ROW EXECUTE FUNCTION d2_project_init();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_relation_insert() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN IF NOT EXISTS(SELECT 1 FROM project_todos p JOIN project_todos c ON c.project_id=p.project_id WHERE p.project_id=NEW.project_id AND p.todo_id=NEW.parent_todo_id AND c.todo_id=NEW.child_todo_id AND p.deleted_at IS NULL AND c.deleted_at IS NULL) OR EXISTS(SELECT 1 FROM project_todo_children WHERE project_id=NEW.project_id AND (child_todo_id=NEW.parent_todo_id OR parent_todo_id=NEW.child_todo_id)) THEN RAISE EXCEPTION 'd2_guard_failure' USING ERRCODE='23514'; END IF; RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_relation_insert BEFORE INSERT ON project_todo_children FOR EACH ROW EXECUTE FUNCTION d2_relation_insert();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_relation_update() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN IF NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.parent_todo_id IS DISTINCT FROM OLD.parent_todo_id OR NEW.child_todo_id IS DISTINCT FROM OLD.child_todo_id OR NEW.created_at IS DISTINCT FROM OLD.created_at OR (NEW.created_by IS DISTINCT FROM OLD.created_by AND NEW.created_by IS NOT NULL) THEN RAISE EXCEPTION 'd2_guard_failure' USING ERRCODE='23514'; END IF; RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_relation_update BEFORE UPDATE ON project_todo_children FOR EACH ROW EXECUTE FUNCTION d2_relation_update();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_result_insert() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN IF NEW.result_state<>'recorded' OR NEW.invalidated_at IS NOT NULL OR NOT EXISTS(SELECT 1 FROM project_todo_children r JOIN project_todos c ON c.project_id=r.project_id AND c.todo_id=r.child_todo_id JOIN project_todos p ON p.project_id=r.project_id AND p.todo_id=r.parent_todo_id JOIN users u ON u.id=NEW.actor_user_id WHERE p.deleted_at IS NULL AND c.deleted_at IS NULL AND r.project_id=NEW.project_id AND r.parent_todo_id=NEW.parent_todo_id AND r.child_todo_id=NEW.child_todo_id AND c.creator_user_id=NEW.actor_user_id) THEN RAISE EXCEPTION 'd2_guard_failure' USING ERRCODE='23514'; END IF; RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_result_insert BEFORE INSERT ON project_todo_child_create_requests FOR EACH ROW EXECUTE FUNCTION d2_result_insert();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_result_update() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN IF NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.parent_todo_id IS DISTINCT FROM OLD.parent_todo_id OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id OR NEW.client_request_id IS DISTINCT FROM OLD.client_request_id OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint OR NEW.child_todo_id IS DISTINCT FROM OLD.child_todo_id OR NEW.created_at IS DISTINCT FROM OLD.created_at OR (OLD.result_state='invalidated' AND (NEW.result_state IS DISTINCT FROM OLD.result_state OR NEW.invalidated_at IS DISTINCT FROM OLD.invalidated_at)) THEN RAISE EXCEPTION 'd2_guard_failure' USING ERRCODE='23514'; END IF; RETURN NEW; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_result_update BEFORE UPDATE ON project_todo_child_create_requests FOR EACH ROW EXECUTE FUNCTION d2_result_update();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_todo_result_invalidate() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN UPDATE project_todo_child_create_requests SET result_state='invalidated',invalidated_at=floor(extract(epoch FROM statement_timestamp()))::BIGINT WHERE project_id=OLD.project_id AND (parent_todo_id=OLD.todo_id OR child_todo_id=OLD.todo_id) AND result_state='recorded'; RETURN OLD; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_todo_result_invalidate BEFORE DELETE ON project_todos FOR EACH ROW EXECUTE FUNCTION d2_todo_result_invalidate();
-- D2_STATEMENT_BOUNDARY
CREATE OR REPLACE FUNCTION d2_user_result_invalidate() RETURNS trigger LANGUAGE plpgsql AS $d2$ BEGIN UPDATE project_todo_child_create_requests SET result_state='invalidated',invalidated_at=floor(extract(epoch FROM statement_timestamp()))::BIGINT WHERE actor_user_id=OLD.id AND result_state='recorded'; RETURN OLD; END; $d2$;
-- D2_STATEMENT_BOUNDARY
CREATE TRIGGER trg_d2_user_result_invalidate BEFORE DELETE ON users FOR EACH ROW EXECUTE FUNCTION d2_user_result_invalidate();
-- D2_STATEMENT_BOUNDARY
INSERT INTO project_todo_children_state(project_id,todo_id,revision,updated_at) SELECT project_id,todo_id,1,floor(extract(epoch FROM statement_timestamp()))::BIGINT FROM project_todos WHERE TRUE ON CONFLICT(project_id,todo_id) DO NOTHING;
-- D2_STATEMENT_BOUNDARY
INSERT INTO project_plan_hierarchy_state(project_id,revision,updated_at) SELECT project_id,1,floor(extract(epoch FROM statement_timestamp()))::BIGINT FROM project_spaces WHERE TRUE ON CONFLICT(project_id) DO NOTHING;
-- D2_STATEMENT_BOUNDARY
UPDATE _schema_version SET version=CASE WHEN version<40 THEN 40 ELSE version END;
