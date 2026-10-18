package svcauth

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"fmt"
	"slices"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	"github.com/illyanyc/strawberry/libs/go/secrets"
)

const authHeader = "authorization"
const scheme = "TB-Service "

// PerRPC returns gRPC per-RPC credentials that attach a fresh token for audience on every call.
func (s *Signer) PerRPC(audience string) grpc.CallOption {
	return grpc.PerRPCCredentials(perRPC{s: s, aud: audience})
}

type perRPC struct {
	s   *Signer
	aud string
}

func (p perRPC) GetRequestMetadata(context.Context, ...string) (map[string]string, error) {
	tok, err := p.s.Token(p.aud)
	if err != nil {
		return nil, err
	}
	return map[string]string{authHeader: scheme + tok}, nil
}

// RequireTransportSecurity is false: calls stay on the internal Docker network (see ADR-0007).
func (perRPC) RequireTransportSecurity() bool { return false }

// UnaryServerInterceptor authenticates every unary call and enforces allowed[fullMethod] (or
// allowed["*"]) as the list of permitted calling services. Unlisted methods are denied.
func (v *Verifier) UnaryServerInterceptor(allowed map[string][]string) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		md, _ := metadata.FromIncomingContext(ctx)
		vals := md.Get(authHeader)
		if len(vals) != 1 || !strings.HasPrefix(vals[0], scheme) {
			return nil, status.Error(codes.Unauthenticated, "service token required")
		}
		claims, err := v.Verify(strings.TrimPrefix(vals[0], scheme))
		if err != nil {
			return nil, status.Error(codes.Unauthenticated, "invalid service token")
		}
		callers, ok := allowed[info.FullMethod]
		if !ok {
			callers = allowed["*"]
		}
		if !slices.Contains(callers, claims.Issuer) {
			return nil, status.Errorf(codes.PermissionDenied, "service %q may not call %s", claims.Issuer, info.FullMethod)
		}
		return handler(WithCaller(ctx, claims.Issuer), req)
	}
}

// StreamServerInterceptor is UnaryServerInterceptor for streaming RPCs.
func (v *Verifier) StreamServerInterceptor(allowed map[string][]string) grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		md, _ := metadata.FromIncomingContext(ss.Context())
		vals := md.Get(authHeader)
		if len(vals) != 1 || !strings.HasPrefix(vals[0], scheme) {
			return status.Error(codes.Unauthenticated, "service token required")
		}
		claims, err := v.Verify(strings.TrimPrefix(vals[0], scheme))
		if err != nil {
			return status.Error(codes.Unauthenticated, "invalid service token")
		}
		callers, ok := allowed[info.FullMethod]
		if !ok {
			callers = allowed["*"]
		}
		if !slices.Contains(callers, claims.Issuer) {
			return status.Errorf(codes.PermissionDenied, "service %q may not call %s", claims.Issuer, info.FullMethod)
		}
		return handler(srv, &callerStream{ServerStream: ss, ctx: WithCaller(ss.Context(), claims.Issuer)})
	}
}

type callerStream struct {
	grpc.ServerStream
	ctx context.Context
}

func (s *callerStream) Context() context.Context { return s.ctx }

// LoadSigner reads the service's private seed from secret "svc_<service>_ed25519" (base64).
func LoadSigner(store *secrets.Store, service string) (*Signer, error) {
	raw, err := store.Get(KeySecretName(service))
	if err != nil {
		return nil, err
	}
	defer secrets.Zero(raw)
	seed, err := base64.StdEncoding.DecodeString(string(raw))
	if err != nil {
		return nil, fmt.Errorf("svcauth: decode seed: %w", err)
	}
	defer secrets.Zero(seed)
	return NewSigner(service, seed)
}

// LoadVerifier builds a verifier for service trusting callers whose public keys are mounted as
// "svc_<caller>_ed25519.pub" (base64).
func LoadVerifier(store *secrets.Store, service string, callers ...string) (*Verifier, error) {
	keys := map[string]ed25519.PublicKey{}
	for _, c := range callers {
		raw, err := store.Get(KeySecretName(c) + ".pub")
		if err != nil {
			return nil, err
		}
		pub, err := base64.StdEncoding.DecodeString(string(raw))
		if err != nil || len(pub) != ed25519.PublicKeySize {
			return nil, fmt.Errorf("svcauth: bad public key for %s", c)
		}
		keys[c] = ed25519.PublicKey(pub)
	}
	return NewVerifier(service, keys), nil
}

// KeySecretName is the secret file name of a service's signing seed.
func KeySecretName(service string) string {
	return "svc_" + strings.ReplaceAll(service, "-", "_") + "_ed25519"
}
