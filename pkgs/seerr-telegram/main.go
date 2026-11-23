// Seerr's requests on Telegram, through a dedicated bot.
//
// Each new request is sent to the configured Telegram chat. One that's waiting
// for approval comes with buttons to approve or decline it, and a show's with
// one to choose which of its seasons to approve (asked for 6, approve the
// first).
//
// Seerr is polled, so nothing needs setting up in it, and Telegram long-polled,
// so nothing is exposed to the internet. Only buttons in the configured chat
// act. Decided in Seerr instead, a request's buttons go away with the outcome.
//
// Credentials (systemd's LoadCredential): "telegram" (TELEGRAM_BOT_TOKEN,
// TELEGRAM_CHAT_ID) and "seerr-settings" (Seerr's settings.json, for its API
// key). Environment: SEERR_URL, SEERR_PUBLIC_URL (for links), STATE_DIRECTORY,
// and TELEGRAM_API (for testing).
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"math/bits"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Seerr request statuses.
const (
	StatusPending   = 1
	StatusApproved  = 2
	StatusDeclined  = 3
	StatusFailed    = 4
	StatusCompleted = 5
)

// PollInterval is how often Seerr is checked for new or updated requests.
const PollInterval = 30 * time.Second

// HTTPError carries an HTTP status code and response body without exposing the
// request URL (which contains the Telegram bot token).
type HTTPError struct {
	Code int
	Body []byte
}

func (e *HTTPError) Error() string {
	var parsed struct {
		Message     string `json:"message"`
		Description string `json:"description"`
	}
	if err := json.Unmarshal(e.Body, &parsed); err == nil {
		if parsed.Message != "" {
			return parsed.Message
		}
		if parsed.Description != "" {
			return parsed.Description
		}
		return ""
	}
	return fmt.Sprintf("error %d", e.Code)
}

func problem(err error) string {
	if err == nil {
		return ""
	}
	var he *HTTPError
	if errors.As(err, &he) {
		return he.Error()
	}
	return err.Error()
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

func callJSON(ctx context.Context, cli *http.Client, method, url string, body any, headers map[string]string, timeout time.Duration, out any) error {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	}
	if method == "" {
		if body != nil {
			method = http.MethodPost
		} else {
			method = http.MethodGet
		}
	}
	reqCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(reqCtx, method, url, r)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
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
		return &HTTPError{Code: resp.StatusCode, Body: raw}
	}
	if len(bytes.TrimSpace(raw)) == 0 || out == nil {
		return nil
	}
	return json.Unmarshal(raw, out)
}

// readEnvFile parses KEY=value lines (shell-sourced style, optional `export `
// prefix and matching single/double quotes).
func readEnvFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	values := make(map[string]string)
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		values[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `'"`)
	}
	return values, sc.Err()
}

// ranges formats [1, 2, 3, 5] as "1–3, 5".
func ranges(numbers []int) string {
	if len(numbers) == 0 {
		return ""
	}
	cp := append([]int(nil), numbers...)
	sort.Ints(cp)

	var parts []string
	start, prev := cp[0], cp[0]
	for _, n := range cp[1:] {
		if n == prev+1 {
			prev = n
			continue
		}
		if start == prev {
			parts = append(parts, strconv.Itoa(start))
		} else {
			parts = append(parts, fmt.Sprintf("%d–%d", start, prev))
		}
		start, prev = n, n
	}
	if start == prev {
		parts = append(parts, strconv.Itoa(start))
	} else {
		parts = append(parts, fmt.Sprintf("%d–%d", start, prev))
	}
	return strings.Join(parts, ", ")
}

// ---- Seerr API ----

type SeerrUser struct {
	DisplayName string `json:"displayName"`
}

type SeerrMedia struct {
	TMDBID int `json:"tmdbId"`
}

type SeerrSeason struct {
	SeasonNumber int `json:"seasonNumber"`
}

type SeerrRequest struct {
	ID                int             `json:"id"`
	Type              string          `json:"type"`
	Status            int             `json:"status"`
	Is4K              *bool           `json:"is4k"`
	ServerID          *int            `json:"serverId"`
	ProfileID         *int            `json:"profileId"`
	RootFolder        *string         `json:"rootFolder"`
	LanguageProfileID *int            `json:"languageProfileId"`
	Tags              json.RawMessage `json:"tags"`
	Media             SeerrMedia      `json:"media"`
	Seasons           []SeerrSeason   `json:"seasons"`
	RequestedBy       SeerrUser       `json:"requestedBy"`
	ModifiedBy        *SeerrUser      `json:"modifiedBy"`
}

func seasonNumbers(r *SeerrRequest) []int {
	if r == nil || len(r.Seasons) == 0 {
		return nil
	}
	out := make([]int, len(r.Seasons))
	for i, s := range r.Seasons {
		out[i] = s.SeasonNumber
	}
	sort.Ints(out)
	return out
}

type MediaDetails struct {
	Name            string
	Year            string
	PosterPath      string
	NumberOfSeasons int
}

type SeerrClient struct {
	baseURL string
	headers map[string]string
	http    *http.Client
}

func NewSeerrClient(baseURL, apiKey string) *SeerrClient {
	return &SeerrClient{
		baseURL: strings.TrimRight(baseURL, "/") + "/api/v1",
		headers: map[string]string{"X-Api-Key": apiKey},
		http:    newCompactHTTPClient(),
	}
}

func (s *SeerrClient) Latest(ctx context.Context) ([]*SeerrRequest, error) {
	var page struct {
		Results []*SeerrRequest `json:"results"`
	}
	if err := callJSON(ctx, s.http, http.MethodGet, s.baseURL+"/request?take=20&sort=added&filter=all", nil, s.headers, 30*time.Second, &page); err != nil {
		return nil, err
	}
	return page.Results, nil
}

func (s *SeerrClient) Request(ctx context.Context, id string) (*SeerrRequest, error) {
	var req SeerrRequest
	err := callJSON(ctx, s.http, http.MethodGet, s.baseURL+"/request/"+id, nil, s.headers, 30*time.Second, &req)
	if err != nil {
		var he *HTTPError
		if errors.As(err, &he) && he.Code == http.StatusNotFound {
			return nil, nil
		}
		return nil, err
	}
	return &req, nil
}

func (s *SeerrClient) Details(ctx context.Context, r *SeerrRequest) (MediaDetails, error) {
	kind := "tv"
	if r.Type == "movie" {
		kind = "movie"
	}
	var found struct {
		Title           string `json:"title"`
		Name            string `json:"name"`
		ReleaseDate     string `json:"releaseDate"`
		FirstAirDate    string `json:"firstAirDate"`
		PosterPath      string `json:"posterPath"`
		NumberOfSeasons int    `json:"numberOfSeasons"`
	}
	path := fmt.Sprintf("/%s/%d", kind, r.Media.TMDBID)
	if err := callJSON(ctx, s.http, http.MethodGet, s.baseURL+path, nil, s.headers, 30*time.Second, &found); err != nil {
		return MediaDetails{}, err
	}
	name := found.Title
	if name == "" {
		name = found.Name
	}
	if name == "" {
		name = "?"
	}
	date := found.ReleaseDate
	if date == "" {
		date = found.FirstAirDate
	}
	year := ""
	if len(date) >= 4 {
		year = date[:4]
	}
	return MediaDetails{
		Name:            name,
		Year:            year,
		PosterPath:      found.PosterPath,
		NumberOfSeasons: found.NumberOfSeasons,
	}, nil
}

func (s *SeerrClient) Decide(ctx context.Context, r *SeerrRequest, approve bool) error {
	status := "decline"
	if approve {
		status = "approve"
	}
	path := fmt.Sprintf("/request/%d/%s", r.ID, status)
	return callJSON(ctx, s.http, http.MethodPost, s.baseURL+path, nil, s.headers, 30*time.Second, nil)
}

// KeepSeasons narrows a pending TV request to the selected seasons.
// Fields left to Seerr's defaults are null and omitted (Seerr rejects null).
func (s *SeerrClient) KeepSeasons(ctx context.Context, r *SeerrRequest, seasons []int) error {
	sorted := append([]int(nil), seasons...)
	sort.Ints(sorted)
	body := map[string]any{
		"mediaType": "tv",
		"seasons":   sorted,
	}
	if r.Is4K != nil {
		body["is4k"] = *r.Is4K
	}
	if r.ServerID != nil {
		body["serverId"] = *r.ServerID
	}
	if r.ProfileID != nil {
		body["profileId"] = *r.ProfileID
	}
	if r.RootFolder != nil {
		body["rootFolder"] = *r.RootFolder
	}
	if r.LanguageProfileID != nil {
		body["languageProfileId"] = *r.LanguageProfileID
	}
	if len(bytes.TrimSpace(r.Tags)) > 0 && string(bytes.TrimSpace(r.Tags)) != "null" {
		body["tags"] = r.Tags
	}
	path := fmt.Sprintf("/request/%d", r.ID)
	return callJSON(ctx, s.http, http.MethodPut, s.baseURL+path, body, s.headers, 30*time.Second, nil)
}

// ---- Telegram API ----

type InlineButton struct {
	Text         string `json:"text"`
	CallbackData string `json:"callback_data,omitempty"`
	URL          string `json:"url,omitempty"`
}

type InlineKeyboard struct {
	InlineKeyboard [][]InlineButton `json:"inline_keyboard"`
}

type TGChat struct {
	ID int64 `json:"id"`
}

type TGMessage struct {
	MessageID int    `json:"message_id"`
	Chat      TGChat `json:"chat"`
	Text      string `json:"text"`
	Caption   string `json:"caption"`
	HasCap    bool   `json:"-"`
}

func (m *TGMessage) UnmarshalJSON(data []byte) error {
	type alias TGMessage
	var a alias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err == nil {
		if v, ok := raw["caption"]; ok && string(bytes.TrimSpace(v)) != "null" {
			a.HasCap = true
		}
	}
	*m = TGMessage(a)
	return nil
}

type TGCallbackQuery struct {
	ID      string     `json:"id"`
	Data    string     `json:"data"`
	Message *TGMessage `json:"message"`
}

type TGUpdate struct {
	UpdateID      int64            `json:"update_id"`
	CallbackQuery *TGCallbackQuery `json:"callback_query"`
}

type TelegramClient struct {
	baseURL string
	http    *http.Client
}

func NewTelegramClient(api, token string) *TelegramClient {
	return &TelegramClient{
		baseURL: fmt.Sprintf("%s/bot%s/", strings.TrimRight(api, "/"), token),
		http:    newCompactHTTPClient(),
	}
}

func (t *TelegramClient) Call(ctx context.Context, method string, params map[string]any, timeout time.Duration, out any) error {
	var envelope struct {
		OK          bool            `json:"ok"`
		Result      json.RawMessage `json:"result"`
		Description string          `json:"description"`
	}
	if err := callJSON(ctx, t.http, http.MethodPost, t.baseURL+method, params, nil, timeout, &envelope); err != nil {
		return err
	}
	if !envelope.OK {
		return fmt.Errorf("telegram %s: %s", method, envelope.Description)
	}
	if out != nil && len(envelope.Result) > 0 {
		return json.Unmarshal(envelope.Result, out)
	}
	return nil
}

// ---- Bot ----

type SentRecord struct {
	Message int    `json:"message"`
	Text    string `json:"text"`
	Photo   bool   `json:"photo"`
}

type State struct {
	Offset int64                 `json:"offset"`
	Latest int                   `json:"latest"`
	Sent   map[string]SentRecord `json:"sent"`
}

func outcome(r *SeerrRequest) string {
	if r == nil {
		return "🗑 Removed in Seerr"
	}
	by := ""
	if r.ModifiedBy != nil && r.ModifiedBy.DisplayName != "" {
		by = " by " + html.EscapeString(r.ModifiedBy.DisplayName)
	}
	switch r.Status {
	case StatusApproved:
		return "✅ Approved" + by
	case StatusDeclined:
		return "❌ Declined" + by
	case StatusFailed:
		return "⚠️ Approved, but sending it to Radarr/Sonarr failed"
	case StatusCompleted:
		return "✅ Available"
	default:
		return ""
	}
}

type Bot struct {
	seerr     *SeerrClient
	telegram  *TelegramClient
	chat      string
	publicURL string
	statePath string
	state     *State
	lastSaved []byte
	nextLook  time.Time
}

func NewBot(seerr *SeerrClient, telegram *TelegramClient, chat, publicURL, statePath string) *Bot {
	b := &Bot{
		seerr:     seerr,
		telegram:  telegram,
		chat:      chat,
		publicURL: strings.TrimRight(publicURL, "/"),
		statePath: statePath,
	}
	if raw, err := os.ReadFile(statePath); err == nil {
		var st State
		if err := json.Unmarshal(raw, &st); err == nil {
			if st.Sent == nil {
				st.Sent = make(map[string]SentRecord)
			}
			b.state = &st
			if normalized, err := json.Marshal(b.state); err == nil {
				b.lastSaved = normalized
			}
		}
	}
	return b
}

func (b *Bot) save() error {
	if b.state == nil {
		return nil
	}
	raw, err := json.Marshal(b.state)
	if err != nil {
		return err
	}
	if bytes.Equal(raw, b.lastSaved) {
		return nil
	}
	tmp := b.statePath + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, b.statePath); err != nil {
		return err
	}
	b.lastSaved = raw
	return nil
}

// Look checks Seerr for new requests and updates any decided in Seerr.
func (b *Bot) Look(ctx context.Context) error {
	requests, err := b.seerr.Latest(ctx)
	if err != nil {
		return err
	}
	sorted := append([]*SeerrRequest(nil), requests...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].ID < sorted[j].ID })

	if b.state == nil {
		// First time: only announce what's waiting now, not the whole history.
		b.state = &State{Sent: make(map[string]SentRecord)}
		for _, r := range sorted {
			if r.ID > b.state.Latest {
				b.state.Latest = r.ID
			}
		}
		for _, r := range sorted {
			if r.Status == StatusPending {
				if err := b.announce(ctx, r); err != nil {
					return err
				}
			}
		}
	}
	for _, r := range sorted {
		if r.ID > b.state.Latest {
			if err := b.announce(ctx, r); err != nil {
				return err
			}
			b.state.Latest = r.ID
		}
	}

	byID := make(map[string]*SeerrRequest, len(requests))
	for _, r := range requests {
		byID[strconv.Itoa(r.ID)] = r
	}
	sentIDs := make([]string, 0, len(b.state.Sent))
	for id := range b.state.Sent {
		sentIDs = append(sentIDs, id)
	}
	sort.Strings(sentIDs)
	for _, id := range sentIDs {
		req := byID[id]
		if req == nil {
			req, err = b.seerr.Request(ctx, id)
			if err != nil {
				return err
			}
		}
		if req == nil || req.Status != StatusPending {
			var link [][]InlineButton
			if req != nil {
				link = b.link(req)
			}
			b.finish(ctx, id, outcome(req), link)
		}
	}
	return b.save()
}

func (b *Bot) describe(ctx context.Context, r *SeerrRequest) (string, string, error) {
	det, err := b.seerr.Details(ctx, r)
	if err != nil {
		return "", "", err
	}
	who := r.RequestedBy.DisplayName
	if who == "" {
		who = "Someone"
	}
	what := fmt.Sprintf("<b>%s</b>", html.EscapeString(det.Name))
	if det.Year != "" {
		what += fmt.Sprintf(" (%s)", det.Year)
	}
	icon := "📺"
	if r.Type == "movie" {
		icon = "🎬"
	}
	text := fmt.Sprintf("%s <b>%s</b> requested %s", icon, html.EscapeString(who), what)
	if r.Type == "tv" {
		seasons := seasonNumbers(r)
		plural := ""
		if len(seasons) > 1 {
			plural = "s"
		}
		text += fmt.Sprintf("\nSeason%s %s", plural, ranges(seasons))
		maxSeason := 0
		if len(seasons) > 0 {
			maxSeason = seasons[len(seasons)-1]
		}
		if det.NumberOfSeasons > 0 && det.NumberOfSeasons >= maxSeason {
			text += fmt.Sprintf(" of %d", det.NumberOfSeasons)
		}
	}
	if r.Is4K != nil && *r.Is4K {
		text += "\nIn 4K"
	}
	return text, det.PosterPath, nil
}

func (b *Bot) link(r *SeerrRequest) [][]InlineButton {
	if b.publicURL == "" {
		return nil
	}
	kind := "tv"
	if r.Type == "movie" {
		kind = "movie"
	}
	return [][]InlineButton{{{
		Text: "Open in Seerr",
		URL:  fmt.Sprintf("%s/%s/%d", b.publicURL, kind, r.Media.TMDBID),
	}}}
}

func (b *Bot) buttons(r *SeerrRequest) InlineKeyboard {
	approveLabel := "✅ Approve"
	seasons := seasonNumbers(r)
	if len(seasons) > 1 {
		approveLabel = "✅ Approve all"
	}
	rows := [][]InlineButton{{
		{Text: approveLabel, CallbackData: fmt.Sprintf("a:%d", r.ID)},
		{Text: "❌ Decline", CallbackData: fmt.Sprintf("d:%d", r.ID)},
	}}
	if len(seasons) > 1 {
		rows = append(rows, []InlineButton{{
			Text:         "✏️ Choose seasons",
			CallbackData: fmt.Sprintf("p:%d:0", r.ID),
		}})
	}
	rows = append(rows, b.link(r)...)
	return InlineKeyboard{InlineKeyboard: rows}
}

func (b *Bot) picker(r *SeerrRequest, chosen uint64) InlineKeyboard {
	seasons := seasonNumbers(r)
	toggles := make([]InlineButton, 0, len(seasons))
	for i, n := range seasons {
		if i >= 64 {
			break
		}
		prefix := "◻️ "
		if (chosen>>uint(i))&1 == 1 {
			prefix = "✅ "
		}
		toggles = append(toggles, InlineButton{
			Text:         fmt.Sprintf("%sS%d", prefix, n),
			CallbackData: fmt.Sprintf("p:%d:%x", r.ID, chosen^(uint64(1)<<uint(i))),
		})
	}
	var rows [][]InlineButton
	for i := 0; i < len(toggles); i += 4 {
		end := i + 4
		if end > len(toggles) {
			end = len(toggles)
		}
		rows = append(rows, toggles[i:end])
	}
	count := bits.OnesCount64(chosen)
	approve := "Choose a season"
	if count > 0 {
		plural := ""
		if count > 1 {
			plural = "s"
		}
		approve = fmt.Sprintf("✅ Approve %d season%s", count, plural)
	}
	rows = append(rows, []InlineButton{
		{Text: approve, CallbackData: fmt.Sprintf("k:%d:%x", r.ID, chosen)},
		{Text: "↩️ Back", CallbackData: fmt.Sprintf("b:%d", r.ID)},
	})
	return InlineKeyboard{InlineKeyboard: rows}
}

func (b *Bot) announce(ctx context.Context, r *SeerrRequest) error {
	text, poster, err := b.describe(ctx, r)
	if err != nil {
		return err
	}
	waiting := r.Status == StatusPending
	if !waiting {
		text += "\n" + outcome(r)
	}
	var markup InlineKeyboard
	if waiting {
		markup = b.buttons(r)
	} else {
		link := b.link(r)
		if link == nil {
			link = [][]InlineButton{}
		}
		markup = InlineKeyboard{InlineKeyboard: link}
	}

	var sent TGMessage
	hasPhoto := false
	if poster != "" {
		params := map[string]any{
			"chat_id":      b.chat,
			"parse_mode":   "HTML",
			"reply_markup": markup,
			"caption":      text,
			"photo":        "https://image.tmdb.org/t/p/w500" + poster,
		}
		if err := b.telegram.Call(ctx, "sendPhoto", params, 30*time.Second, &sent); err != nil {
			var he *HTTPError
			if errors.As(err, &he) {
				fmt.Printf("sending the poster failed: %s\n", problem(err))
			} else {
				return err
			}
		} else {
			hasPhoto = true
		}
	}
	if !hasPhoto {
		params := map[string]any{
			"chat_id":      b.chat,
			"parse_mode":   "HTML",
			"reply_markup": markup,
			"text":         text,
		}
		if err := b.telegram.Call(ctx, "sendMessage", params, 30*time.Second, &sent); err != nil {
			return err
		}
	}
	if waiting {
		b.state.Sent[strconv.Itoa(r.ID)] = SentRecord{
			Message: sent.MessageID,
			Text:    text,
			Photo:   hasPhoto,
		}
	}
	return nil
}

func (b *Bot) finish(ctx context.Context, requestID, line string, link [][]InlineButton) {
	sent, ok := b.state.Sent[requestID]
	if !ok {
		return
	}
	delete(b.state.Sent, requestID)

	if link == nil {
		link = [][]InlineButton{}
	}
	text := sent.Text + "\n\n" + line
	params := map[string]any{
		"chat_id":      b.chat,
		"message_id":   sent.Message,
		"parse_mode":   "HTML",
		"reply_markup": InlineKeyboard{InlineKeyboard: link},
	}
	method := "editMessageText"
	if sent.Photo {
		method = "editMessageCaption"
		params["caption"] = text
	} else {
		params["text"] = text
	}
	if err := b.telegram.Call(ctx, method, params, 30*time.Second, nil); err != nil {
		fmt.Printf("couldn't update request %s: %s\n", requestID, problem(err))
	}
}

// Listen waits for button presses until it's time to look at Seerr again.
func (b *Bot) Listen(ctx context.Context) error {
	wait := int(time.Until(b.nextLook).Seconds())
	if wait < 1 {
		wait = 1
	}
	if wait > 25 {
		wait = 25
	}
	params := map[string]any{
		"timeout":         wait,
		"offset":          b.state.Offset,
		"allowed_updates": []string{"callback_query"},
	}
	var updates []TGUpdate
	if err := b.telegram.Call(ctx, "getUpdates", params, time.Duration(wait+10)*time.Second, &updates); err != nil {
		return err
	}
	for _, u := range updates {
		b.state.Offset = u.UpdateID + 1
		if u.CallbackQuery != nil {
			b.pressed(ctx, u.CallbackQuery)
		}
	}
	if len(updates) > 0 {
		return b.save()
	}
	return nil
}

func (b *Bot) pressed(ctx context.Context, q *TGCallbackQuery) {
	chatID := ""
	if q.Message != nil {
		chatID = strconv.FormatInt(q.Message.Chat.ID, 10)
	}
	if chatID != b.chat {
		b.answer(ctx, q, "This isn't your server's chat", false)
		return
	}
	parts := strings.Split(q.Data, ":")
	if len(parts) < 2 {
		b.answer(ctx, q, "", false)
		return
	}
	action, reqID := parts[0], parts[1]
	chosen := ""
	if len(parts) >= 3 {
		chosen = parts[2]
	}
	if _, err := strconv.Atoi(reqID); err != nil || reqID == "" {
		b.answer(ctx, q, "", false)
		return
	}
	if err := b.act(ctx, q, action, reqID, chosen); err != nil {
		var he *HTTPError
		if errors.As(err, &he) {
			b.answer(ctx, q, "Seerr said no: "+problem(err), true)
		} else {
			b.answer(ctx, q, "Couldn't reach Seerr: "+err.Error(), true)
		}
	}
}

func (b *Bot) act(ctx context.Context, q *TGCallbackQuery, action, reqID, chosenHex string) error {
	b.remember(reqID, q.Message)
	req, err := b.seerr.Request(ctx, reqID)
	if err != nil {
		return err
	}
	if req == nil || req.Status != StatusPending {
		b.finish(ctx, reqID, outcome(req), nil)
		b.answer(ctx, q, "It was decided in Seerr already", false)
		return nil
	}
	msgID := q.Message.MessageID
	switch action {
	case "a", "d":
		if err := b.seerr.Decide(ctx, req, action == "a"); err != nil {
			return err
		}
		updated, err := b.seerr.Request(ctx, reqID)
		if err != nil {
			return err
		}
		b.finish(ctx, reqID, outcome(updated), b.link(req))
		b.answer(ctx, q, "Done", false)
	case "p":
		mask, err := strconv.ParseUint(chosenHex, 16, 64)
		if err != nil {
			b.answer(ctx, q, "", false)
			return nil
		}
		if err := b.telegram.Call(ctx, "editMessageReplyMarkup", map[string]any{
			"chat_id":      b.chat,
			"message_id":   msgID,
			"reply_markup": b.picker(req, mask),
		}, 30*time.Second, nil); err != nil {
			return err
		}
		b.answer(ctx, q, "", false)
	case "b":
		if err := b.telegram.Call(ctx, "editMessageReplyMarkup", map[string]any{
			"chat_id":      b.chat,
			"message_id":   msgID,
			"reply_markup": b.buttons(req),
		}, 30*time.Second, nil); err != nil {
			return err
		}
		b.answer(ctx, q, "", false)
	case "k":
		mask, err := strconv.ParseUint(chosenHex, 16, 64)
		if err != nil {
			b.answer(ctx, q, "", false)
			return nil
		}
		seasons := seasonNumbers(req)
		var keep []int
		for i, n := range seasons {
			if i < 64 && (mask>>uint(i))&1 == 1 {
				keep = append(keep, n)
			}
		}
		if len(keep) == 0 {
			b.answer(ctx, q, "Choose a season first", false)
			return nil
		}
		if !slicesEqual(keep, seasons) {
			if err := b.seerr.KeepSeasons(ctx, req, keep); err != nil {
				return err
			}
		}
		if err := b.seerr.Decide(ctx, req, true); err != nil {
			return err
		}
		updated, err := b.seerr.Request(ctx, reqID)
		if err != nil {
			return err
		}
		plural := ""
		if len(keep) > 1 {
			plural = "s"
		}
		b.finish(ctx, reqID, fmt.Sprintf("%s: season%s %s", outcome(updated), plural, ranges(keep)), b.link(req))
		b.answer(ctx, q, "Done", false)
	default:
		b.answer(ctx, q, "", false)
	}
	return nil
}

func slicesEqual(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func (b *Bot) remember(reqID string, msg *TGMessage) {
	if _, exists := b.state.Sent[reqID]; exists || msg == nil {
		return
	}
	raw := msg.Text
	if msg.HasCap {
		raw = msg.Caption
	}
	b.state.Sent[reqID] = SentRecord{
		Message: msg.MessageID,
		Text:    html.EscapeString(raw),
		Photo:   msg.HasCap,
	}
}

func (b *Bot) answer(ctx context.Context, q *TGCallbackQuery, text string, alert bool) {
	err := b.telegram.Call(ctx, "answerCallbackQuery", map[string]any{
		"callback_query_id": q.ID,
		"text":              text,
		"show_alert":        alert,
	}, 30*time.Second, nil)
	if err != nil {
		fmt.Printf("couldn't answer a button: %s\n", problem(err))
	}
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

func (b *Bot) Run(ctx context.Context) {
	for ctx.Err() == nil {
		if !time.Now().Before(b.nextLook) {
			b.nextLook = time.Now().Add(PollInterval)
			if err := b.Look(ctx); err != nil && ctx.Err() == nil {
				fmt.Printf("couldn't look at Seerr: %s\n", problem(err))
			}
		}
		if b.state == nil {
			if !sleepCtx(ctx, PollInterval) {
				return
			}
			continue
		}
		if err := b.Listen(ctx); err != nil && ctx.Err() == nil {
			fmt.Printf("couldn't reach Telegram: %s\n", problem(err))
			if !sleepCtx(ctx, 10*time.Second) {
				return
			}
		}
	}
}

func credentialPath(name string) string {
	return filepath.Join(os.Getenv("CREDENTIALS_DIRECTORY"), name)
}

func main() {
	telegramEnv, err := readEnvFile(credentialPath("telegram"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed reading telegram credential: %v\n", err)
		os.Exit(1)
	}
	token := telegramEnv["TELEGRAM_BOT_TOKEN"]
	chatID := telegramEnv["TELEGRAM_CHAT_ID"]
	if token == "" || chatID == "" {
		fmt.Fprintln(os.Stderr, "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in telegram credential")
		os.Exit(1)
	}

	rawSettings, err := os.ReadFile(credentialPath("seerr-settings"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed reading seerr-settings credential: %v\n", err)
		os.Exit(1)
	}
	var settings struct {
		Main struct {
			APIKey string `json:"apiKey"`
		} `json:"main"`
	}
	if err := json.Unmarshal(rawSettings, &settings); err != nil || settings.Main.APIKey == "" {
		fmt.Fprintf(os.Stderr, "failed reading Seerr API key from seerr-settings: %v\n", err)
		os.Exit(1)
	}

	seerrURL := os.Getenv("SEERR_URL")
	if seerrURL == "" {
		seerrURL = "http://127.0.0.1:5055"
	}
	telegramAPI := os.Getenv("TELEGRAM_API")
	if telegramAPI == "" {
		telegramAPI = "https://api.telegram.org"
	}
	stateDir := os.Getenv("STATE_DIRECTORY")
	if stateDir == "" {
		stateDir = "."
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	bot := NewBot(
		NewSeerrClient(seerrURL, settings.Main.APIKey),
		NewTelegramClient(telegramAPI, token),
		chatID,
		os.Getenv("SEERR_PUBLIC_URL"),
		filepath.Join(stateDir, "state.json"),
	)
	bot.Run(ctx)
}
