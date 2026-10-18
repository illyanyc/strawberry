package lake

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

// PGManifest stores the manifest in marketdata.lake_files (ST-003).
type PGManifest struct {
	Pool *pgxpool.Pool
}

// Record implements Manifest.
func (m PGManifest) Record(ctx context.Context, f FileInfo) error {
	_, err := m.Pool.Exec(ctx, `INSERT INTO marketdata.lake_files (path, dataset, partition, kind, row_count, bytes, sha256, min_ts, max_ts)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
		ON CONFLICT (path) DO UPDATE SET row_count = EXCLUDED.row_count, bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256,
		  min_ts = EXCLUDED.min_ts, max_ts = EXCLUDED.max_ts, removed_at = NULL`,
		f.Path, f.Dataset, f.Partition, f.Kind, f.Rows, f.Bytes, f.SHA256, f.MinTs, f.MaxTs)
	return err
}

// Remove implements Manifest.
func (m PGManifest) Remove(ctx context.Context, path string) error {
	_, err := m.Pool.Exec(ctx, `UPDATE marketdata.lake_files SET removed_at = now() WHERE path = $1 AND removed_at IS NULL`, path)
	return err
}

// Live implements Manifest.
func (m PGManifest) Live(ctx context.Context) ([]FileInfo, error) {
	rows, err := m.Pool.Query(ctx, `SELECT path, dataset, partition, kind, row_count, bytes, sha256, min_ts, max_ts
		FROM marketdata.lake_files WHERE removed_at IS NULL ORDER BY path`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []FileInfo
	for rows.Next() {
		var f FileInfo
		if err := rows.Scan(&f.Path, &f.Dataset, &f.Partition, &f.Kind, &f.Rows, &f.Bytes, &f.SHA256, &f.MinTs, &f.MaxTs); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}
