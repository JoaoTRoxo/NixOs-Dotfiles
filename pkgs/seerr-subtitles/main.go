// Subtitles requested through Seerr, fetched by Bazarr, with nobody approving.
//
// Jelly's Request Subtitles opens an issue about subtitles in Seerr, whose
// message ends with Bazarr's code for the language: "Requesting subtitles in
// Portuguese [pt]." This goes through the open ones and has Bazarr search for
// whatever each covers (a show, a season, an episode or a movie) that doesn't
// have that language yet. Once it's all there, the issue is resolved. Issues
// written by hand have no code, and are left for a person.
//
// Providers run out for the day (OpenSubtitles' downloads, say), so what isn't
// found is tried again a day later, for as long as tries keep finding some.
// After Tries in a row that find nothing, the issue gets a comment saying
// what's missing, and stays open.
//
// Bazarr can't search for anything without a language profile, and most of the
// library has none. So what has none borrows the first profile for the search,
// and gives it back.
//
// Credentials (systemd's LoadCredential): "seerr-settings" (Seerr's
// settings.json) and "bazarr-config" (Bazarr's config.yaml). Environment:
// SEERR_URL, BAZARR_URL, and STATE_DIRECTORY.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"
)

const (
	SubtitlesIssue = 3
	PollEvery      = 60 * time.Second
	RetryAfter     = 24 * time.Hour
	Tries          = 3
	SearchTimeout  = 600 * time.Second
	JobPoll        = 3 * time.Second
	NamedLimit     = 10
)

var requestRe = regexp.MustCompile(`^Requesting subtitles in (.+) \[([a-z]{2})\]\.$`)

type HTTPError struct {
	Code int
	URL  string
}

func (e *HTTPError) Error() string {
	base, _, _ := strings.Cut(e.URL, "?")
	return fmt.Sprintf("error %d from %s", e.Code, base)
}

func newCompactHTTPClient() *http.Client {
	return &http.Client{
		Transport: &http.Transport{
			Proxy:                 http.ProxyFromEnvironment,
			MaxIdleConns:          4,
			MaxIdleConnsPerHost:   2,
			IdleConnTimeout:       90 * time.Second,
			TLSHandshakeTimeout:   10 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
		},
	}
}

func callJSON(ctx context.Context, cli *http.Client, method, rawURL string, body any, headers map[string]string, out any) error {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	}
	reqCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(reqCtx, method, rawURL, r)
	if err != nil {
		return err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := cli.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return &HTTPError{Code: resp.StatusCode, URL: rawURL}
	}
	if len(bytes.TrimSpace(raw)) == 0 || out == nil {
		return nil
	}
	return json.Unmarshal(raw, out)
}

// bazarrKey reads auth.apikey from Bazarr's config.yaml without pulling in a
// YAML dependency (other sections also have apikey fields).
func bazarrKey(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	section := ""
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if line == "" {
			continue
		}
		r := rune(line[0])
		if !unicode.IsSpace(r) {
			section = strings.TrimSuffix(strings.TrimSpace(line), ":")
		} else if section == "auth" {
			trimmed := strings.TrimSpace(line)
			if strings.HasPrefix(trimmed, "apikey:") {
				_, val, _ := strings.Cut(trimmed, ":")
				return strings.Trim(strings.TrimSpace(val), `'"`), nil
			}
		}
	}
	if err := sc.Err(); err != nil {
		return "", err
	}
	return "", errors.New("no auth.apikey in Bazarr's config")
}

// ---- Seerr API ----

type IssueComment struct {
	Message string `json:"message"`
}

type IssueMedia struct {
	MediaType         string `json:"mediaType"`
	ExternalServiceID *int   `json:"externalServiceId"`
}

type SeerrIssue struct {
	ID             int            `json:"id"`
	IssueType      int            `json:"issueType"`
	Status         int            `json:"status"`
	ProblemSeason  int            `json:"problemSeason"`
	ProblemEpisode int            `json:"problemEpisode"`
	Media          IssueMedia     `json:"media"`
	Comments       []IssueComment `json:"comments"`
}

type SeerrClient struct {
	baseURL string
	headers map[string]string
	http    *http.Client
}

func NewSeerrClient(baseURL, apiKey string) *SeerrClient {
	return &SeerrClient{
		baseURL: strings.TrimRight(baseURL, "/") + "/api/v1",
		headers: map[string]string{
			"X-Api-Key":    apiKey,
			"Content-Type": "application/json",
		},
		http: newCompactHTTPClient(),
	}
}

func (s *SeerrClient) OpenSubtitleIssues(ctx context.Context) ([]SeerrIssue, error) {
	var page struct {
		Results []SeerrIssue `json:"results"`
	}
	if err := callJSON(ctx, s.http, http.MethodGet, s.baseURL+"/issue?take=100&skip=0&sort=added&filter=open", nil, s.headers, &page); err != nil {
		return nil, err
	}
	var out []SeerrIssue
	// Oldest first (Seerr returns newest first).
	for i := len(page.Results) - 1; i >= 0; i-- {
		if page.Results[i].IssueType == SubtitlesIssue {
			out = append(out, page.Results[i])
		}
	}
	return out, nil
}

func (s *SeerrClient) Comment(ctx context.Context, issueID int, message string) error {
	path := fmt.Sprintf("/issue/%d/comment", issueID)
	return callJSON(ctx, s.http, http.MethodPost, s.baseURL+path, map[string]string{"message": message}, s.headers, nil)
}

func (s *SeerrClient) Resolve(ctx context.Context, issueID int) error {
	path := fmt.Sprintf("/issue/%d/resolved", issueID)
	return callJSON(ctx, s.http, http.MethodPost, s.baseURL+path, nil, s.headers, nil)
}

// ---- Bazarr API ----

type Item struct {
	Kind string
	ID   int
}

func (it Item) MarshalJSON() ([]byte, error) {
	return json.Marshal([]any{it.Kind, it.ID})
}

func (it *Item) UnmarshalJSON(data []byte) error {
	var raw []json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil || len(raw) != 2 {
		return fmt.Errorf("invalid borrowed item: %s", string(data))
	}
	if err := json.Unmarshal(raw[0], &it.Kind); err != nil {
		return err
	}
	return json.Unmarshal(raw[1], &it.ID)
}

type Subtitle struct {
	Code2  string `json:"code2"`
	Forced bool   `json:"forced"`
}

type Video struct {
	SonarrEpisodeID int        `json:"sonarrEpisodeId"`
	RadarrID        int        `json:"radarrId"`
	Season          *int       `json:"season"`
	Episode         *int       `json:"episode"`
	ProfileID       *int       `json:"profileId"`
	Subtitles       []Subtitle `json:"subtitles"`
}

type BazarrClient struct {
	baseURL string
	headers map[string]string
	http    *http.Client
	poll    time.Duration
}

func NewBazarrClient(baseURL, apiKey string) *BazarrClient {
	return &BazarrClient{
		baseURL: strings.TrimRight(baseURL, "/") + "/api",
		headers: map[string]string{"X-API-KEY": apiKey},
		http:    newCompactHTTPClient(),
		poll:    JobPoll,
	}
}

func (b *BazarrClient) send(ctx context.Context, method, path string, query url.Values, out any) error {
	u := b.baseURL + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	return callJSON(ctx, b.http, method, u, nil, b.headers, out)
}

func (b *BazarrClient) Videos(ctx context.Context, item Item) ([]Video, error) {
	var page struct {
		Data []Video `json:"data"`
	}
	q := url.Values{}
	if item.Kind == "tv" {
		q.Set("seriesid[]", strconv.Itoa(item.ID))
		if err := b.send(ctx, http.MethodGet, "/episodes", q, &page); err != nil {
			return nil, err
		}
	} else {
		q.Set("radarrid[]", strconv.Itoa(item.ID))
		if err := b.send(ctx, http.MethodGet, "/movies", q, &page); err != nil {
			return nil, err
		}
	}
	return page.Data, nil
}

func (b *BazarrClient) HasProfile(ctx context.Context, item Item) (bool, error) {
	var page struct {
		Data []struct {
			ProfileID *int `json:"profileId"`
		} `json:"data"`
	}
	q := url.Values{}
	path := "/movies"
	if item.Kind == "tv" {
		q.Set("seriesid[]", strconv.Itoa(item.ID))
		path = "/series"
	} else {
		q.Set("radarrid[]", strconv.Itoa(item.ID))
	}
	if err := b.send(ctx, http.MethodGet, path, q, &page); err != nil {
		return false, err
	}
	return len(page.Data) > 0 && page.Data[0].ProfileID != nil && *page.Data[0].ProfileID != 0, nil
}

func (b *BazarrClient) FirstProfile(ctx context.Context) (string, error) {
	var profiles []struct {
		ProfileID int `json:"profileId"`
	}
	if err := b.send(ctx, http.MethodGet, "/system/languages/profiles", nil, &profiles); err != nil {
		return "", err
	}
	if len(profiles) == 0 {
		return "", errors.New("no language profiles in Bazarr")
	}
	return strconv.Itoa(profiles[0].ProfileID), nil
}

func (b *BazarrClient) SetProfile(ctx context.Context, item Item, profile string) error {
	q := url.Values{"profileid": {profile}}
	if item.Kind == "tv" {
		q.Set("seriesid", strconv.Itoa(item.ID))
		return b.send(ctx, http.MethodPost, "/series", q, nil)
	}
	q.Set("radarrid", strconv.Itoa(item.ID))
	return b.send(ctx, http.MethodPost, "/movies", q, nil)
}

func (b *BazarrClient) Search(ctx context.Context, item Item, video Video, code string) error {
	q := url.Values{
		"language": {code},
		"forced":   {"False"},
		"hi":       {"False"},
	}
	if item.Kind == "tv" {
		q.Set("seriesid", strconv.Itoa(item.ID))
		q.Set("episodeid", strconv.Itoa(video.SonarrEpisodeID))
		return b.send(ctx, http.MethodPatch, "/episodes/subtitles", q, nil)
	}
	q.Set("radarrid", strconv.Itoa(item.ID))
	return b.send(ctx, http.MethodPatch, "/movies/subtitles", q, nil)
}

func (b *BazarrClient) Searching(ctx context.Context) (bool, error) {
	var page struct {
		Data []struct {
			Status  string `json:"status"`
			JobName string `json:"job_name"`
		} `json:"data"`
	}
	if err := b.send(ctx, http.MethodGet, "/system/jobs", nil, &page); err != nil {
		return false, err
	}
	for _, job := range page.Data {
		if (job.Status == "pending" || job.Status == "running") && strings.HasPrefix(job.JobName, "Searching") {
			return true, nil
		}
	}
	return false, nil
}

func (b *BazarrClient) Wait(ctx context.Context) error {
	deadline := time.Now().Add(SearchTimeout)
	for time.Now().Before(deadline) {
		busy, err := b.Searching(ctx)
		if err != nil {
			return err
		}
		if !busy {
			return nil
		}
		if b.poll > 0 {
			if !sleepCtx(ctx, b.poll) {
				return ctx.Err()
			}
		}
	}
	return nil
}

// ---- Matching Helpers ----

func wanted(issue SeerrIssue) (name, code string, ok bool) {
	if len(issue.Comments) == 0 {
		return "", "", false
	}
	m := requestRe.FindStringSubmatch(strings.TrimSpace(issue.Comments[0].Message))
	if m == nil {
		return "", "", false
	}
	return m[1], m[2], true
}

func covered(v Video, season, episode int) bool {
	if season != 0 && (v.Season == nil || *v.Season != season) {
		return false
	}
	if episode != 0 && (v.Episode == nil || *v.Episode != episode) {
		return false
	}
	return true
}

func hasSubtitle(v Video, code string) bool {
	for _, s := range v.Subtitles {
		if s.Code2 == code && !s.Forced {
			return true
		}
	}
	return false
}

func named(videos []Video) string {
	var eps []Video
	for _, v := range videos {
		if v.Season != nil && v.Episode != nil {
			eps = append(eps, v)
		}
	}
	if len(eps) == 0 {
		return ""
	}
	sort.Slice(eps, func(i, j int) bool {
		if *eps[i].Season != *eps[j].Season {
			return *eps[i].Season < *eps[j].Season
		}
		return *eps[i].Episode < *eps[j].Episode
	})
	limit := len(eps)
	if limit > NamedLimit {
		limit = NamedLimit
	}
	names := make([]string, limit)
	for i := 0; i < limit; i++ {
		names[i] = fmt.Sprintf("S%dE%d", *eps[i].Season, *eps[i].Episode)
	}
	more := ""
	if len(eps) > NamedLimit {
		more = fmt.Sprintf(" and %d more", len(eps)-NamedLimit)
	}
	return fmt.Sprintf(" for %s%s", strings.Join(names, ", "), more)
}

// ---- Worker ----

type IssueRecord struct {
	GivenUp bool    `json:"given_up,omitempty"`
	Idle    int     `json:"idle,omitempty"`
	Next    float64 `json:"next,omitempty"`
}

type State struct {
	Issues   map[string]*IssueRecord `json:"issues"`
	Borrowed []Item                  `json:"borrowed"`
}

type Worker struct {
	seerr     *SeerrClient
	bazarr    *BazarrClient
	statePath string
	state     State
	lastSaved []byte
	now       func() float64
}

func NewWorker(seerr *SeerrClient, bazarr *BazarrClient, statePath string) *Worker {
	w := &Worker{
		seerr:     seerr,
		bazarr:    bazarr,
		statePath: statePath,
		state: State{
			Issues:   make(map[string]*IssueRecord),
			Borrowed: []Item{},
		},
		now: func() float64 { return float64(time.Now().UnixNano()) / 1e9 },
	}
	if raw, err := os.ReadFile(statePath); err == nil {
		var st State
		if err := json.Unmarshal(raw, &st); err == nil {
			if st.Issues == nil {
				st.Issues = make(map[string]*IssueRecord)
			}
			if st.Borrowed == nil {
				st.Borrowed = []Item{}
			}
			w.state = st
			if normalized, err := json.Marshal(w.state); err == nil {
				w.lastSaved = normalized
			}
		}
	}
	return w
}

func (w *Worker) save() error {
	raw, err := json.Marshal(w.state)
	if err != nil {
		return err
	}
	if bytes.Equal(raw, w.lastSaved) {
		return nil
	}
	tmp := w.statePath + ".new"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, w.statePath); err != nil {
		return err
	}
	w.lastSaved = raw
	return nil
}

func (w *Worker) giveBack(ctx context.Context) error {
	for len(w.state.Borrowed) > 0 {
		item := w.state.Borrowed[0]
		if err := w.bazarr.SetProfile(ctx, item, "null"); err != nil {
			return err
		}
		w.state.Borrowed = w.state.Borrowed[1:]
		if err := w.save(); err != nil {
			return err
		}
	}
	return nil
}

func (w *Worker) search(ctx context.Context, item Item, videos []Video, code string) (retErr error) {
	hasProf, err := w.bazarr.HasProfile(ctx, item)
	if err != nil {
		return err
	}
	if !hasProf {
		// Record before borrowing so a crash still gives it back on next tick.
		w.state.Borrowed = append(w.state.Borrowed, item)
		if err := w.save(); err != nil {
			return err
		}
		prof, err := w.bazarr.FirstProfile(ctx)
		if err != nil {
			return err
		}
		if err := w.bazarr.SetProfile(ctx, item, prof); err != nil {
			return err
		}
	}
	defer func() {
		if err := w.giveBack(ctx); err != nil && retErr == nil {
			retErr = err
		}
	}()
	for _, v := range videos {
		if err := w.bazarr.Search(ctx, item, v, code); err != nil {
			return err
		}
		if err := w.bazarr.Wait(ctx); err != nil {
			return err
		}
	}
	return nil
}

func (w *Worker) isDue(issue SeerrIssue) bool {
	rec := w.state.Issues[strconv.Itoa(issue.ID)]
	if rec == nil {
		return true
	}
	return !rec.GivenUp && w.now() >= rec.Next
}

func (w *Worker) giveUp(ctx context.Context, issue SeerrIssue, message string) error {
	if err := w.seerr.Comment(ctx, issue.ID, message); err != nil {
		return err
	}
	w.state.Issues[strconv.Itoa(issue.ID)] = &IssueRecord{GivenUp: true}
	if err := w.save(); err != nil {
		return err
	}
	fmt.Printf("issue %d: gave up: %s\n", issue.ID, message)
	return nil
}

func (w *Worker) done(ctx context.Context, issue SeerrIssue, message string) error {
	if err := w.seerr.Comment(ctx, issue.ID, message); err != nil {
		return err
	}
	if err := w.seerr.Resolve(ctx, issue.ID); err != nil {
		return err
	}
	delete(w.state.Issues, strconv.Itoa(issue.ID))
	if err := w.save(); err != nil {
		return err
	}
	fmt.Printf("issue %d: %s\n", issue.ID, message)
	return nil
}

func (w *Worker) fulfil(ctx context.Context, issue SeerrIssue, name, code string) error {
	if issue.Media.ExternalServiceID == nil {
		return w.giveUp(ctx, issue, "Sonarr or Radarr doesn't have this.")
	}
	item := Item{Kind: issue.Media.MediaType, ID: *issue.Media.ExternalServiceID}

	fetchVideos := func() ([]Video, error) {
		all, err := w.bazarr.Videos(ctx, item)
		if err != nil {
			return nil, err
		}
		var matched []Video
		for _, v := range all {
			if covered(v, issue.ProblemSeason, issue.ProblemEpisode) {
				matched = append(matched, v)
			}
		}
		return matched, nil
	}
	without := func(videos []Video) []Video {
		var out []Video
		for _, v := range videos {
			if !hasSubtitle(v, code) {
				out = append(out, v)
			}
		}
		return out
	}

	found, err := fetchVideos()
	if err != nil {
		return err
	}
	if len(found) == 0 {
		return w.giveUp(ctx, issue, "Bazarr doesn't have this.")
	}
	missing := without(found)
	if len(missing) == 0 {
		return w.done(ctx, issue, fmt.Sprintf("These %s subtitles were already here.", name))
	}
	fmt.Printf("issue %d: searching %s for %d of %s/%d\n", issue.ID, name, len(missing), item.Kind, item.ID)
	if err := w.search(ctx, item, missing, code); err != nil {
		return err
	}
	after, err := fetchVideos()
	if err != nil {
		return err
	}
	left := without(after)
	if len(left) == 0 {
		return w.done(ctx, issue, fmt.Sprintf("Found the %s subtitles.", name))
	}
	key := strconv.Itoa(issue.ID)
	rec := w.state.Issues[key]
	if rec == nil {
		rec = &IssueRecord{}
		w.state.Issues[key] = rec
	}
	if len(left) < len(missing) {
		rec.Idle = 0
	} else {
		rec.Idle++
	}
	if rec.Idle >= Tries {
		return w.giveUp(ctx, issue, fmt.Sprintf("Couldn't find %s subtitles%s.", name, named(left)))
	}
	rec.Next = w.now() + RetryAfter.Seconds()
	return w.save()
}

func (w *Worker) Tick(ctx context.Context) error {
	if err := w.giveBack(ctx); err != nil {
		return err
	}
	issues, err := w.seerr.OpenSubtitleIssues(ctx)
	if err != nil {
		return err
	}
	for _, issue := range issues {
		name, code, ok := wanted(issue)
		if ok && w.isDue(issue) {
			if err := w.fulfil(ctx, issue, name, code); err != nil {
				return err
			}
		}
	}
	stillOpen := make(map[string]struct{}, len(issues))
	for _, issue := range issues {
		stillOpen[strconv.Itoa(issue.ID)] = struct{}{}
	}
	pruned := false
	for k := range w.state.Issues {
		if _, ok := stillOpen[k]; !ok {
			delete(w.state.Issues, k)
			pruned = true
		}
	}
	if pruned {
		return w.save()
	}
	return nil
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (w *Worker) Run(ctx context.Context) {
	for ctx.Err() == nil {
		if err := w.Tick(ctx); err != nil && ctx.Err() == nil {
			fmt.Printf("trying again later: %v\n", err)
		}
		if !sleepCtx(ctx, PollEvery) {
			return
		}
	}
}

func credentialPath(name string) string {
	return filepath.Join(os.Getenv("CREDENTIALS_DIRECTORY"), name)
}

func main() {
	rawSeerr, err := os.ReadFile(credentialPath("seerr-settings"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed reading seerr-settings credential: %v\n", err)
		os.Exit(1)
	}
	var settings struct {
		Main struct {
			APIKey string `json:"apiKey"`
		} `json:"main"`
	}
	if err := json.Unmarshal(rawSeerr, &settings); err != nil || settings.Main.APIKey == "" {
		fmt.Fprintf(os.Stderr, "failed reading Seerr API key from seerr-settings: %v\n", err)
		os.Exit(1)
	}

	bKey, err := bazarrKey(credentialPath("bazarr-config"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed reading Bazarr API key: %v\n", err)
		os.Exit(1)
	}

	seerrURL := os.Getenv("SEERR_URL")
	if seerrURL == "" {
		seerrURL = "http://127.0.0.1:5055"
	}
	bazarrURL := os.Getenv("BAZARR_URL")
	if bazarrURL == "" {
		bazarrURL = "http://127.0.0.1:6767"
	}
	stateDir := os.Getenv("STATE_DIRECTORY")
	if stateDir == "" {
		stateDir = "."
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	w := NewWorker(
		NewSeerrClient(seerrURL, settings.Main.APIKey),
		NewBazarrClient(bazarrURL, bKey),
		filepath.Join(stateDir, "state.json"),
	)
	w.Run(ctx)
}
