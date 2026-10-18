package simcore

import (
	"bufio"
	"context"
	"errors"
	"io"

	"google.golang.org/protobuf/encoding/protodelim"

	backtestv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/backtest/v1"
)

// maxMessage bounds one framed request (a setup with a large universe is the biggest).
const maxMessage = 64 << 20

// Serve answers length-delimited SimRequests from r with one length-delimited SimResponse each on w
// until r reaches EOF (tb.backtest.v1 simcore transport).
func Serve(ctx context.Context, c *Core, r io.Reader, w io.Writer) error {
	in := bufio.NewReaderSize(r, 1<<16)
	out := bufio.NewWriterSize(w, 1<<16)
	opts := protodelim.UnmarshalOptions{MaxSize: maxMessage}
	for {
		req := &backtestv1.SimRequest{}
		if err := opts.UnmarshalFrom(in, req); err != nil {
			if errors.Is(err, io.EOF) {
				return out.Flush()
			}
			return err
		}
		if _, err := protodelim.MarshalTo(out, c.Handle(ctx, req)); err != nil {
			return err
		}
		if err := out.Flush(); err != nil {
			return err
		}
	}
}
