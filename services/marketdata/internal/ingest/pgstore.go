package ingest

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/encoding/protojson"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
)

// PGStore persists backfill state in schema marketdata (MD-001).
type PGStore struct {
	Pool *pgxpool.Pool
}

func nullTime(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t
}

// SaveJob implements Store.
func (s PGStore) SaveJob(ctx context.Context, j *Job) error {
	rep := []byte("{}")
	if j.Report != nil {
		var err error
		if rep, err = protojson.Marshal(j.Report); err != nil {
			return err
		}
	}
	tfs := make([]string, len(j.Timeframes))
	for i, tf := range j.Timeframes {
		tfs[i] = tf.String()
	}
	_, err := s.Pool.Exec(ctx, `INSERT INTO marketdata.backfill_jobs (job_id, source, timeframes, instruments, start_ns, end_ns, state, report, error, started_at, finished_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
		ON CONFLICT (job_id) DO UPDATE SET state=EXCLUDED.state, report=EXCLUDED.report, error=EXCLUDED.error,
		  started_at=COALESCE(EXCLUDED.started_at, marketdata.backfill_jobs.started_at), finished_at=EXCLUDED.finished_at`,
		j.ID, j.Source, tfs, j.Instruments, j.Start.UnixNano(), j.End.UnixNano(), j.State.String(), rep, j.Err, nullTime(j.StartedAt), nullTime(j.FinishedAt))
	return err
}

func scanJob(row pgx.Row) (*Job, error) {
	var j Job
	var tfs []string
	var state string
	var rep []byte
	var s, e int64
	var started, finished *time.Time
	if err := row.Scan(&j.ID, &j.Source, &tfs, &j.Instruments, &s, &e, &state, &rep, &j.Err, &started, &finished); err != nil {
		return nil, err
	}
	j.Start, j.End = time.Unix(0, s).UTC(), time.Unix(0, e).UTC()
	j.State = mdv1.JobState(mdv1.JobState_value[state])
	for _, tf := range tfs {
		j.Timeframes = append(j.Timeframes, mdv1.Timeframe(mdv1.Timeframe_value[tf]))
	}
	j.Report = &mdv1.BackfillReport{}
	if err := protojson.Unmarshal(rep, j.Report); err != nil {
		return nil, err
	}
	if started != nil {
		j.StartedAt = *started
	}
	if finished != nil {
		j.FinishedAt = *finished
	}
	return &j, nil
}

const jobCols = `job_id, source, timeframes, instruments, start_ns, end_ns, state, report, error, started_at, finished_at`

// LoadJob implements Store.
func (s PGStore) LoadJob(ctx context.Context, id string) (*Job, error) {
	j, err := scanJob(s.Pool.QueryRow(ctx, `SELECT `+jobCols+` FROM marketdata.backfill_jobs WHERE job_id = $1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errors.New("backfill: unknown job")
	}
	return j, err
}

// ChunkDone implements Store.
func (s PGStore) ChunkDone(ctx context.Context, jobID, key string) (bool, error) {
	var ok bool
	err := s.Pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM marketdata.backfill_chunks WHERE job_id=$1 AND chunk_key=$2)`, jobID, key).Scan(&ok)
	return ok, err
}

// MarkChunk implements Store.
func (s PGStore) MarkChunk(ctx context.Context, jobID, key string, rows int64) error {
	_, err := s.Pool.Exec(ctx, `INSERT INTO marketdata.backfill_chunks (job_id, chunk_key, rows) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, jobID, key, rows)
	return err
}

// SaveGap implements Store.
func (s PGStore) SaveGap(ctx context.Context, g GapRecord) error {
	_, err := s.Pool.Exec(ctx, `INSERT INTO marketdata.gaps (job_id, source, instrument_id, timeframe, from_ns, to_ns, missing, status)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`, g.JobID, g.Source, g.InstrumentID, g.Timeframe, g.From.UnixNano(), g.To.UnixNano(), g.Missing, g.Status)
	return err
}

// Unfinished implements Store.
func (s PGStore) Unfinished(ctx context.Context) ([]*Job, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+jobCols+` FROM marketdata.backfill_jobs WHERE state IN ('JOB_STATE_QUEUED','JOB_STATE_RUNNING') ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*Job
	for rows.Next() {
		j, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, rows.Err()
}
