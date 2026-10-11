CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name VARCHAR(100) NOT NULL,
 email VARCHAR(150) UNIQUE NOT NULL,
 phone VARCHAR(20) UNIQUE NULL,
 student_id VARCHAR(40) UNIQUE NULL,
 password VARCHAR(255) NOT NULL,
 role VARCHAR(20) NOT NULL DEFAULT 'user',
 reward_public BOOLEAN NOT NULL DEFAULT FALSE,
 reward_public_name VARCHAR(80) DEFAULT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 item_name VARCHAR(150) NOT NULL,
 category VARCHAR(80) NOT NULL,
 description TEXT NOT NULL,
 location VARCHAR(200) NOT NULL,
 event_date DATE NOT NULL,
 image VARCHAR(255),
 status VARCHAR(32) NOT NULL,
 icon VARCHAR(80) DEFAULT 'fa-box',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 brand VARCHAR(100) DEFAULT NULL,
 color VARCHAR(60) DEFAULT NULL,
 is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
 qr_token CHAR(36) DEFAULT NULL UNIQUE,
 event_time TIME DEFAULT NULL,
 city VARCHAR(100) DEFAULT NULL,
 state VARCHAR(100) DEFAULT NULL,
 approx_lat REAL DEFAULT NULL,
 approx_lng REAL DEFAULT NULL,
 private_details TEXT DEFAULT NULL,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS item_embeddings (
 item_id INT NOT NULL,
 model VARCHAR(120) NOT NULL,
 text_hash CHAR(64) NOT NULL,
 embedding TEXT NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (item_id, model),
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claims (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INT NOT NULL,
 user_id INT NOT NULL,
 proof TEXT NOT NULL,
 proof_image VARCHAR(255) DEFAULT NULL,
 proof_file VARCHAR(255) DEFAULT NULL,
 status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Under Review','Approved','Rejected','Returned')),
 handover_at DATETIME DEFAULT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 message VARCHAR(255) NOT NULL,
 is_read BOOLEAN DEFAULT FALSE,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 notification_type VARCHAR(20) NOT NULL DEFAULT 'general',
 item_id INT DEFAULT NULL,
 matched_item_id INT DEFAULT NULL,
 match_score INTEGER DEFAULT NULL,
 explanation TEXT DEFAULT NULL,
 claim_id INT DEFAULT NULL,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 actor_id INT NULL,
 action VARCHAR(80) NOT NULL,
 entity_type VARCHAR(40) NOT NULL,
 entity_id INT NULL,
 details TEXT,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS handover_receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INT NOT NULL UNIQUE,
 claim_id INT NOT NULL,
 receiver_id INT NOT NULL,
 receiver_name VARCHAR(100) NOT NULL,
 receiver_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
 authorized_staff_id INT NOT NULL,
 authorized_staff_name VARCHAR(100) NOT NULL,
 confirmed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
 FOREIGN KEY (claim_id) REFERENCES claims(id) ON DELETE CASCADE,
 FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY (authorized_staff_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS journey_places (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id INT NOT NULL,
 user_id INT NOT NULL,
 place_name VARCHAR(180) NOT NULL,
 city VARCHAR(100) DEFAULT NULL,
 state VARCHAR(100) DEFAULT NULL,
 visited_at DATETIME NOT NULL,
 approx_lat REAL DEFAULT NULL,
 approx_lng REAL DEFAULT NULL,
 notes VARCHAR(500) DEFAULT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_journey_places_owner_item_time
 ON journey_places(user_id,item_id,visited_at);

CREATE TABLE IF NOT EXISTS location_prediction_plans (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 plan_json TEXT NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_location_prediction_plans_owner_date
 ON location_prediction_plans(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS search_missions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 item_id INT NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'Active'
   CHECK (status IN ('Active','Paused','Completed','Successful','Archived')),
 last_reminded_at TIMESTAMP DEFAULT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_missions_owner_status
 ON search_missions(user_id,status,updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_search_missions_active_item
 ON search_missions(user_id,item_id) WHERE status IN ('Active','Paused');

CREATE TABLE IF NOT EXISTS search_mission_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mission_id INT NOT NULL,
 title VARCHAR(180) NOT NULL,
 details VARCHAR(500) DEFAULT NULL,
 priority VARCHAR(10) NOT NULL DEFAULT 'Medium'
   CHECK (priority IN ('High','Medium','Low')),
 status VARCHAR(20) NOT NULL DEFAULT 'Not Started'
   CHECK (status IN ('Not Started','In Progress','Completed','Not Applicable')),
 is_custom BOOLEAN NOT NULL DEFAULT FALSE,
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (mission_id) REFERENCES search_missions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_mission_tasks_mission_order
 ON search_mission_tasks(mission_id,sort_order,id);

CREATE TABLE IF NOT EXISTS reward_transactions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 contribution_type VARCHAR(40) NOT NULL,
 recovery_id INT DEFAULT NULL,
 contribution_id INT DEFAULT NULL,
 points INTEGER NOT NULL CHECK (points <> 0),
 reason VARCHAR(500) NOT NULL,
 created_by INT DEFAULT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY (recovery_id) REFERENCES items(id) ON DELETE SET NULL,
 FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_successful_return
 ON reward_transactions(user_id,recovery_id)
 WHERE contribution_type='successful_return' AND recovery_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_clue_contribution
 ON reward_transactions(user_id,contribution_id)
 WHERE contribution_type='helpful_clue' AND contribution_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_return_reversal
 ON reward_transactions(user_id,recovery_id)
 WHERE contribution_type='reversal' AND recovery_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS reward_badges (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name VARCHAR(80) NOT NULL UNIQUE,
 successful_returns INTEGER NOT NULL UNIQUE CHECK (successful_returns > 0),
 active BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT OR IGNORE INTO reward_badges(name,successful_returns) VALUES
 ('Helpful Member',1),('Trusted Finder',5),('Community Hero',15),('FindIt Champion',30);

CREATE TABLE IF NOT EXISTS user_reward_badges (
 user_id INT NOT NULL,
 badge_id INT NOT NULL,
 awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(user_id,badge_id),
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY (badge_id) REFERENCES reward_badges(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reward_contributions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INT NOT NULL,
 item_id INT NOT NULL,
 contribution_type VARCHAR(40) NOT NULL,
 details VARCHAR(1000) NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'Pending'
  CHECK (status IN ('Pending','Approved','Rejected')),
 reviewed_by INT DEFAULT NULL,
 reviewed_at TIMESTAMP DEFAULT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
 FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL,
 UNIQUE(user_id,item_id,contribution_type)
);

-- After registering your first account, promote it to administrator in the Staff panel.
