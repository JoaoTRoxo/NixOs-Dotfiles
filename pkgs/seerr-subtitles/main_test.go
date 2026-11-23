package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
)

const (
	showID  = 16
	movieID = 5
	ptMsg   = "Requesting subtitles in Portuguese [pt]."
)

type bazarrCall struct {
	method string
	path   string
	query  url.Values
}

type fakeJob struct {
	JobName string     `json:"job_name"`
	Status  string     `json:"status"`
	Query   url.Values `json:"-"`
}

type fakeComment struct {
	id  int
	msg string
}

type fakeWorld struct {
	mu             sync.Mutex
	issues         map[int]*SeerrIssue
	comments       []fakeComment
	calls          []bazarrCall
	searches       []url.Values
	jobs           []*fakeJob
	shows          map[int]*int
	movies         map[int]*int
	episodes       []*Video
	movieSubtitles []Subtitle
	unavailable    map[string]bool
	quota          *int
	withoutProfile int
	refuse         bool
}

func intPtr(v int) *int { return &v }

func makeEp(num, season int, subs ...Subtitle) *Video {
	if subs == nil {
		subs = []Subtitle{}
	}
	return &Video{
		SonarrEpisodeID: season*100 + num,
		Season:          intPtr(season),
		Episode:         intPtr(num),
		Subtitles:       subs,
	}
}

func makeIssue(id int, kind string, svc *int, season, ep int, msg string, itype int) *SeerrIssue {
	return &SeerrIssue{
		ID:             id,
		IssueType:      itype,
		Status:         1,
		ProblemSeason:  season,
		ProblemEpisode: ep,
		Media:          IssueMedia{MediaType: kind, ExternalServiceID: svc},
		Comments:       []IssueComment{{Message: msg}},
	}
}

type harness struct {
	t      *testing.T
	world  *fakeWorld
	srv    *httptest.Server
	state  string
	clock  float64
	worker *Worker
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	w := &fakeWorld{
		issues:      make(map[int]*SeerrIssue),
		shows:       map[int]*int{showID: nil},
		movies:      map[int]*int{movieID: nil},
		unavailable: make(map[string]bool),
	}
	srv := httptest.NewServer(http.HandlerFunc(w.serveHTTP))
	t.Cleanup(srv.Close)

	h := &harness{
		t:     t,
		world: w,
		srv:   srv,
		state: filepath.Join(t.TempDir(), "state.json"),
		clock: 1000.0,
	}
	h.worker = h.start()
	return h
}

func (h *harness) start() *Worker {
	b := NewBazarrClient(h.srv.URL, "bazarr-key")
	b.poll = 0
	w := NewWorker(NewSeerrClient(h.srv.URL, "seerr-key"), b, h.state)
	w.now = func() float64 { return h.clock }
	return w
}

func (h *harness) ask(i *SeerrIssue) *SeerrIssue {
	h.world.mu.Lock()
	defer h.world.mu.Unlock()
	h.world.issues[i.ID] = i
	return i
}

func (h *harness) searched() []int {
	h.world.mu.Lock()
	defer h.world.mu.Unlock()
	out := make([]int, 0, len(h.world.searches))
	for _, q := range h.world.searches {
		id, _ := strconv.Atoi(q.Get("episodeid"))
		out = append(out, id)
	}
	return out
}

func (h *harness) resolved(id int) bool {
	h.world.mu.Lock()
	defer h.world.mu.Unlock()
	return h.world.issues[id].Status == 2
}

func (w *fakeWorld) reply(rw http.ResponseWriter, code int, body any) {
	rw.Header().Set("Content-Type", "application/json")
	rw.WriteHeader(code)
	if body != nil {
		_ = json.NewEncoder(rw).Encode(body)
	}
}

func (w *fakeWorld) serveHTTP(rw http.ResponseWriter, r *http.Request) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if strings.HasPrefix(r.URL.Path, "/api/v1/") {
		if r.Header.Get("X-Api-Key") != "seerr-key" {
			w.reply(rw, 403, map[string]any{"message": "no"})
			return
		}
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/v1/"), "/")
		if r.Method == http.MethodGet {
			var open []*SeerrIssue
			for _, iss := range w.issues {
				if iss.Status == 1 {
					open = append(open, iss)
				}
			}
			sort.Slice(open, func(i, j int) bool { return open[i].ID > open[j].ID })
			w.reply(rw, 200, map[string]any{"results": open})
			return
		}
		id, _ := strconv.Atoi(parts[1])
		if parts[2] == "comment" {
			var body struct {
				Message string `json:"message"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			w.comments = append(w.comments, fakeComment{id: id, msg: body.Message})
		} else {
			w.issues[id].Status = 2
		}
		w.reply(rw, 200, map[string]any{})
		return
	}

	// Bazarr API
	q := r.URL.Query()
	w.calls = append(w.calls, bazarrCall{method: r.Method, path: r.URL.Path, query: q})
	if r.Header.Get("X-API-KEY") != "bazarr-key" {
		w.reply(rw, 401, nil)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/")
	if r.Method == http.MethodPatch {
		if w.refuse {
			w.reply(rw, 500, nil)
			return
		}
		w.searches = append(w.searches, q)
		w.jobs = append(w.jobs, &fakeJob{JobName: "Searching subtitles", Status: "pending", Query: q})
		w.reply(rw, 204, nil)
		return
	}
	if r.Method == http.MethodPost {
		var prof *int
		if p := q.Get("profileid"); p != "null" {
			n, _ := strconv.Atoi(p)
			prof = &n
		}
		if path == "series" {
			k, _ := strconv.Atoi(q.Get("seriesid"))
			w.shows[k] = prof
		} else {
			k, _ := strconv.Atoi(q.Get("radarrid"))
			w.movies[k] = prof
		}
		w.reply(rw, 204, nil)
		return
	}
	switch path {
	case "system/jobs":
		snap := make([]fakeJob, len(w.jobs))
		for i, j := range w.jobs {
			snap[i] = *j
		}
		w.reply(rw, 200, map[string]any{"data": snap})
		w.runJobsLocked()
	case "system/languages/profiles":
		w.reply(rw, 200, []map[string]any{{"profileId": 1}, {"profileId": 2}})
	case "episodes":
		w.reply(rw, 200, map[string]any{"data": w.episodes})
	case "series":
		k, _ := strconv.Atoi(q.Get("seriesid[]"))
		w.reply(rw, 200, map[string]any{"data": []map[string]any{{"profileId": w.shows[k]}}})
	default:
		k, _ := strconv.Atoi(q.Get("radarrid[]"))
		w.reply(rw, 200, map[string]any{"data": []map[string]any{{
			"radarrId":  k,
			"profileId": w.movies[k],
			"subtitles": w.movieSubtitles,
		}}})
	}
}

func (w *fakeWorld) runJobsLocked() {
	for _, job := range w.jobs {
		if job.Status != "pending" {
			continue
		}
		job.Status = "completed"
		q := job.Query
		code := q.Get("language")
		isMovie := q.Get("radarrid") != ""
		var prof *int
		if isMovie {
			k, _ := strconv.Atoi(q.Get("radarrid"))
			prof = w.movies[k]
		} else {
			k, _ := strconv.Atoi(q.Get("seriesid"))
			prof = w.shows[k]
		}
		if prof == nil {
			w.withoutProfile++
			continue
		}
		if w.quota != nil {
			if *w.quota <= 0 {
				continue
			}
			*w.quota--
		}
		if isMovie {
			if !w.unavailable["movie"] {
				w.movieSubtitles = append(w.movieSubtitles, Subtitle{Code2: code})
			}
			continue
		}
		epID, _ := strconv.Atoi(q.Get("episodeid"))
		if !w.unavailable[strconv.Itoa(epID)] {
			for _, ep := range w.episodes {
				if ep.SonarrEpisodeID == epID {
					ep.Subtitles = append(ep.Subtitles, Subtitle{Code2: code})
				}
			}
		}
	}
}

func TestFindsAnEpisode(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1), makeEp(2, 1)}
	h.ask(makeIssue(1, "tv", intPtr(showID), 1, 2, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(h.searched(), []int{102}) {
		t.Fatalf("searched=%v", h.searched())
	}
	q := h.world.searches[0]
	if q.Get("seriesid") != strconv.Itoa(showID) || q.Get("language") != "pt" || q.Get("forced") != "False" || q.Get("hi") != "False" {
		t.Fatalf("unexpected search query: %v", q)
	}
	if len(h.world.episodes[1].Subtitles) != 1 || len(h.world.episodes[0].Subtitles) != 0 {
		t.Fatal("unexpected subtitles state")
	}
	if !reflect.DeepEqual(h.world.comments, []fakeComment{{1, "Found the Portuguese subtitles."}}) {
		t.Fatalf("comments=%v", h.world.comments)
	}
	if !h.resolved(1) {
		t.Fatal("expected resolved")
	}
}

func TestWholeShowOnlySearchesMissing(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{
		makeEp(1, 1, Subtitle{Code2: "pt"}),
		makeEp(2, 1, Subtitle{Code2: "pt", Forced: true}),
		makeEp(3, 1, Subtitle{Code2: "en"}),
		makeEp(1, 2),
	}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	got := h.searched()
	sort.Ints(got)
	if !reflect.DeepEqual(got, []int{102, 103, 201}) || !h.resolved(1) {
		t.Fatalf("searched=%v resolved=%v", got, h.resolved(1))
	}
}

func TestSeasonOnlySearchesThatSeason(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1), makeEp(1, 2), makeEp(2, 2)}
	h.ask(makeIssue(1, "tv", intPtr(showID), 2, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	got := h.searched()
	sort.Ints(got)
	if !reflect.DeepEqual(got, []int{201, 202}) {
		t.Fatalf("searched=%v", got)
	}
}

func TestFindsMovie(t *testing.T) {
	h := newHarness(t)
	h.ask(makeIssue(1, "movie", intPtr(movieID), 0, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if h.world.searches[0].Get("radarrid") != strconv.Itoa(movieID) {
		t.Fatal("wrong radarrid")
	}
	if len(h.world.movieSubtitles) != 1 || !h.resolved(1) || h.world.movies[movieID] != nil {
		t.Fatal("unexpected movie result")
	}
}

func TestAlreadyThereOnlyResolves(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1, Subtitle{Code2: "pt"})}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(h.world.searches) != 0 || h.world.shows[showID] != nil || !h.resolved(1) {
		t.Fatal("should resolve without searching")
	}
	if !reflect.DeepEqual(h.world.comments, []fakeComment{{1, "These Portuguese subtitles were already here."}}) {
		t.Fatalf("comments=%v", h.world.comments)
	}
}

func TestLeavesManualIssues(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1)}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, "They're out of sync", SubtitlesIssue))
	h.ask(makeIssue(2, "tv", intPtr(showID), 0, 0, ptMsg, 2))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(h.world.searches) != 0 || len(h.world.comments) != 0 || h.resolved(1) || h.resolved(2) {
		t.Fatal("manual issues should be untouched")
	}
}

func TestBorrowsAndReturnsProfile(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1)}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if h.world.withoutProfile != 0 || h.world.shows[showID] != nil || len(h.worker.state.Borrowed) != 0 {
		t.Fatal("profile not borrowed/returned cleanly")
	}
}

func TestKeepsExistingProfile(t *testing.T) {
	h := newHarness(t)
	h.world.shows[showID] = intPtr(2)
	h.world.episodes = []*Video{makeEp(1, 1)}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	if err := h.worker.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if *h.world.shows[showID] != 2 || !h.resolved(1) {
		t.Fatal("existing profile should remain 2")
	}
	for _, c := range h.world.calls {
		if c.method == http.MethodPost {
			t.Fatal("should not POST profile when show already has one")
		}
	}
}

func TestGivesProfileBackOnErrorAndAfterCrash(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1)}
	h.world.refuse = true
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	err := h.worker.Tick(context.Background())
	var he *HTTPError
	if !errors.As(err, &he) {
		t.Fatalf("expected HTTPError, got %v", err)
	}
	if h.world.shows[showID] != nil || len(h.worker.state.Borrowed) != 0 {
		t.Fatal("profile not returned after search error")
	}

	// Simulate crash with borrowed profile persisted in state.json
	h.world.refuse = false
	h.world.shows[showID] = intPtr(1)
	h.worker.state.Borrowed = append(h.worker.state.Borrowed, Item{Kind: "tv", ID: showID})
	_ = h.worker.save()
	h.world.issues = map[int]*SeerrIssue{}
	if err := h.start().Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if h.world.shows[showID] != nil {
		t.Fatal("profile not returned after crash recovery")
	}
}

func TestRetriesDailyThenGivesUpAndQuotaProgress(t *testing.T) {
	h := newHarness(t)
	h.world.episodes = []*Video{makeEp(1, 1)}
	h.world.unavailable["101"] = true
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	_ = h.worker.Tick(context.Background())
	h.clock += RetryAfter.Seconds() - 1
	_ = h.worker.Tick(context.Background())
	if len(h.searched()) != 1 {
		t.Fatalf("should not retry before 24h: %v", h.searched())
	}
	for i := 0; i < Tries-1; i++ {
		h.clock += RetryAfter.Seconds()
		_ = h.worker.Tick(context.Background())
	}
	if len(h.searched()) != Tries || h.resolved(1) {
		t.Fatalf("searched=%v resolved=%v", h.searched(), h.resolved(1))
	}
	if !reflect.DeepEqual(h.world.comments, []fakeComment{{1, "Couldn't find Portuguese subtitles for S1E1."}}) {
		t.Fatalf("comments=%v", h.world.comments)
	}
	// Stays quiet afterwards
	h.clock += 10 * RetryAfter.Seconds()
	_ = h.worker.Tick(context.Background())
	if len(h.searched()) != Tries {
		t.Fatal("should not search again after giving up")
	}

	// Quota progress keeps going across > Tries days
	h2 := newHarness(t)
	for n := 1; n <= 5; n++ {
		h2.world.episodes = append(h2.world.episodes, makeEp(n, 1))
	}
	h2.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	for i := 0; i < 4; i++ {
		q := 1
		h2.world.quota = &q
		_ = h2.worker.Tick(context.Background())
		h2.clock += RetryAfter.Seconds()
		if h2.resolved(1) {
			t.Fatal("should not be resolved yet")
		}
	}
	q := 1
	h2.world.quota = &q
	_ = h2.worker.Tick(context.Background())
	if !h2.resolved(1) {
		t.Fatal("expected resolved once all episodes found")
	}
}

func TestManyMissingAndMovieNotFoundAndPruneAndOrder(t *testing.T) {
	h := newHarness(t)
	for n := 1; n <= 13; n++ {
		h.world.episodes = append(h.world.episodes, makeEp(n, 1))
		h.world.unavailable[strconv.Itoa(100+n)] = true
	}
	h.ask(makeIssue(1, "tv", intPtr(showID), 0, 0, ptMsg, SubtitlesIssue))
	for i := 0; i < Tries; i++ {
		_ = h.worker.Tick(context.Background())
		h.clock += RetryAfter.Seconds()
	}
	said := h.world.comments[0].msg
	if !strings.Contains(said, "S1E1, S1E2") || !strings.HasSuffix(said, "S1E10 and 3 more.") {
		t.Fatalf("unexpected summary: %s", said)
	}

	// Closed externally gets pruned
	h.world.issues[1].Status = 2
	_ = h.worker.Tick(context.Background())
	if len(h.worker.state.Issues) != 0 {
		t.Fatalf("expected pruned issues, got %v", h.worker.state.Issues)
	}

	// Oldest first
	h2 := newHarness(t)
	h2.world.episodes = []*Video{makeEp(1, 1), makeEp(2, 1)}
	h2.ask(makeIssue(7, "tv", intPtr(showID), 1, 2, ptMsg, SubtitlesIssue))
	h2.ask(makeIssue(3, "tv", intPtr(showID), 1, 1, ptMsg, SubtitlesIssue))
	_ = h2.worker.Tick(context.Background())
	if !reflect.DeepEqual(h2.searched(), []int{101, 102}) {
		t.Fatalf("expected oldest first [101 102], got %v", h2.searched())
	}
}

func TestBazarrKeyAndWanted(t *testing.T) {
	p := filepath.Join(t.TempDir(), "config.yaml")
	_ = os.WriteFile(p, []byte("sonarr:\n  apikey: not-this\nauth:\n  type: null\n  apikey: 'the-key'\nradarr:\n  apikey: nor-this\n"), 0o600)
	got, err := bazarrKey(p)
	if err != nil || got != "the-key" {
		t.Fatalf("bazarrKey=%q err=%v", got, err)
	}
	name, code, ok := wanted(*makeIssue(1, "tv", intPtr(showID), 0, 0, "Requesting subtitles in Portuguese (Brazil) [pb].", SubtitlesIssue))
	if !ok || name != "Portuguese (Brazil)" || code != "pb" {
		t.Fatalf("wanted=(%q,%q,%v)", name, code, ok)
	}
	if _, _, ok := wanted(*makeIssue(1, "tv", intPtr(showID), 0, 0, "Portuguese please", SubtitlesIssue)); ok {
		t.Fatal("should ignore manual text")
	}
}
