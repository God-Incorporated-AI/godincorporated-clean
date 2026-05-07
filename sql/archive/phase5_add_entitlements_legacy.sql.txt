ALTER TABLE users
ADD COLUMN plan_code text NOT NULL DEFAULT 'dormant',
ADD COLUMN plan_started_at timestamp with time zone DEFAULT now(),
ADD COLUMN plan_expires_at timestamp with time zone,
ADD COLUMN is_admin boolean NOT NULL DEFAULT false,
ADD COLUMN is_banned boolean NOT NULL DEFAULT false;