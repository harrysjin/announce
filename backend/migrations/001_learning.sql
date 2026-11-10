-- Private learning data; no PDF bytes, filenames, page text or glyph geometry.
CREATE TABLE learning_consents (
    subject_id text PRIMARY KEY CHECK (length(subject_id) = 64),
    enabled boolean NOT NULL DEFAULT false,
    policy_version text NOT NULL,
    granted_at timestamptz,
    withdrawn_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE learning_events (
    event_id uuid PRIMARY KEY,
    subject_id text NOT NULL REFERENCES learning_consents(subject_id) ON DELETE CASCADE,
    idempotency_key uuid NOT NULL,
    request_hash text NOT NULL,
    job_id text NOT NULL,
    document_group text NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('meaning_selected','reading_selected','manual_edit','undo','redo','visual_edit','note_edit')),
    compensates_event_id uuid REFERENCES learning_events(event_id) ON DELETE SET NULL,
    engine_version text NOT NULL,
    build_commit text,
    before_revision integer NOT NULL,
    after_revision integer NOT NULL,
    changes jsonb NOT NULL,
    visual_changes jsonb NOT NULL,
    consent_version text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(subject_id, idempotency_key),
    UNIQUE(compensates_event_id)
);
CREATE INDEX learning_events_owner_time ON learning_events(subject_id, recorded_at DESC, event_id);
CREATE TABLE learning_cases (
    case_id uuid PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES learning_events(event_id) ON DELETE CASCADE,
    subject_id text NOT NULL REFERENCES learning_consents(subject_id) ON DELETE CASCADE,
    job_id text NOT NULL,
    occurrence_id text NOT NULL,
    document_group text NOT NULL,
    surface text NOT NULL,
    lemma text NOT NULL,
    pos text NOT NULL,
    context text NOT NULL CHECK (length(context) <= 160),
    snapshot jsonb NOT NULL,
    active boolean NOT NULL DEFAULT true,
    status text NOT NULL DEFAULT 'selected' CHECK (status IN ('selected','pending_review','approved','rejected','needs_review')),
    meaning_status text NOT NULL DEFAULT 'pending' CHECK (meaning_status IN ('pending','approved','rejected','needs_review')),
    length_status text NOT NULL DEFAULT 'pending' CHECK (length_status IN ('pending','approved','rejected','needs_review')),
    unresolved_reason text NOT NULL,
    meaning_supported boolean NOT NULL,
    length_supported boolean NOT NULL,
    review_version integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(event_id, occurrence_id),
    CHECK (status <> 'approved' OR (meaning_status='approved' AND length_status='approved' AND meaning_supported AND length_supported))
);
CREATE UNIQUE INDEX learning_case_current ON learning_cases(subject_id, job_id, occurrence_id) WHERE active;
CREATE INDEX learning_case_queue ON learning_cases(status, created_at, case_id) WHERE active;
CREATE INDEX learning_case_recommendation ON learning_cases(surface, lemma, pos) WHERE active AND status='approved';
CREATE INDEX learning_case_document ON learning_cases(document_group);
CREATE INDEX learning_case_context ON learning_cases USING gin(to_tsvector('simple', context));
CREATE TABLE learning_reviews (
    review_id uuid PRIMARY KEY,
    case_id uuid NOT NULL REFERENCES learning_cases(case_id) ON DELETE CASCADE,
    reviewer_id text NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    before_status jsonb NOT NULL,
    after_status jsonb NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);
