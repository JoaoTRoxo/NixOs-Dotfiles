package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
)

type tgCall struct {
	method string
	params map[string]any
}

type fakeWorld struct {
	mu         sync.Mutex
	requests   map[int]map[string]any
	calls      []tgCall
	updates    []map[string]any
	put        []map[string]any
	messageID  int
	photoFails bool
	updateID   int
}

func newFakeWorld(t *testing.T) (*fakeWorld, *httptest.Server, *Bot) {
	t.Helper()
	w := &fakeWorld{
		requests: make(map[int]map[string]any),
	}
	srv := httptest.NewServer(http.HandlerFunc(w.serveHTTP))
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	b := NewBot(
		NewSeerrClient(srv.URL, "key"),
		NewTelegramClient(srv.URL, "token"),
		"-42",
		"https://requests.example",
		filepath.Join(dir, "state.json"),
	)
	return w, srv, b
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

	var body map[string]any
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}

	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/bottoken/") {
		method := strings.TrimPrefix(r.URL.Path, "/bottoken/")
		w.calls = append(w.calls, tgCall{method: method, params: body})
		if method == "sendPhoto" && w.photoFails {
			w.reply(rw, 400, map[string]any{"ok": false, "description": "bad photo"})
			return
		}
		if method == "getUpdates" {
			updates := w.updates
			w.updates = nil
			if updates == nil {
				updates = []map[string]any{}
			}
			w.reply(rw, 200, map[string]any{"ok": true, "result": updates})
			return
		}
		w.messageID++
		w.reply(rw, 200, map[string]any{"ok": true, "result": map[string]any{"message_id": w.messageID}})
		return
	}

	// Seerr API
	if r.Header.Get("X-Api-Key") != "key" {
		w.reply(rw, 401, map[string]any{"message": "no"})
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/v1")
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) == 1 && parts[0] == "request" {
		list := make([]map[string]any, 0, len(w.requests))
		for _, req := range w.requests {
			list = append(list, req)
		}
		sort.Slice(list, func(i, j int) bool { return list[i]["id"].(int) > list[j]["id"].(int) })
		if len(list) > 20 {
			list = list[:20]
		}
		w.reply(rw, 200, map[string]any{"results": list})
		return
	}
	if parts[0] == "movie" || parts[0] == "tv" {
		tmdb, _ := strconv.Atoi(parts[1])
		found := map[string]any{}
		if tmdb != 404 {
			found["posterPath"] = "/p.jpg"
		}
		if parts[0] == "movie" {
			found["title"] = "Dune & Co"
			found["releaseDate"] = "2024-02-27"
		} else {
			found["name"] = "The Bear"
			found["firstAirDate"] = "2022-06-23"
			found["numberOfSeasons"] = 6
		}
		w.reply(rw, 200, found)
		return
	}
	id, _ := strconv.Atoi(parts[1])
	req, ok := w.requests[id]
	if !ok {
		w.reply(rw, 404, map[string]any{"message": "Request not found"})
		return
	}
	if r.Method == http.MethodGet {
		w.reply(rw, 200, req)
		return
	}
	if req["status"].(int) != StatusPending {
		w.reply(rw, 409, map[string]any{"message": "Only pending requests"})
		return
	}
	if r.Method == http.MethodPut {
		// Seerr rejects null for optional fields.
		for _, k := range []string{"is4k", "serverId", "profileId", "languageProfileId", "rootFolder"} {
			if v, present := body[k]; present && v == nil {
				w.reply(rw, 400, map[string]any{"message": "request/body/" + k + " must not be null"})
				return
			}
		}
		w.put = append(w.put, body)
		var seasons []map[string]any
		for _, s := range body["seasons"].([]any) {
			seasons = append(seasons, map[string]any{"seasonNumber": int(s.(float64))})
		}
		req["seasons"] = seasons
		w.reply(rw, 200, req)
		return
	}
	if parts[2] == "approve" {
		req["status"] = StatusApproved
	} else {
		req["status"] = StatusDeclined
	}
	req["modifiedBy"] = map[string]any{"id": 1, "displayName": "aurea"}
	w.reply(rw, 200, req)
}

func (w *fakeWorld) add(id int, kind string, status int, seasons []int, extra map[string]any) map[string]any {
	w.mu.Lock()
	defer w.mu.Unlock()
	sList := make([]map[string]any, 0, len(seasons))
	for _, n := range seasons {
		sList = append(sList, map[string]any{"seasonNumber": n})
	}
	req := map[string]any{
		"id":                id,
		"type":              kind,
		"status":            status,
		"is4k":              false,
		"serverId":          nil,
		"profileId":         nil,
		"rootFolder":        nil,
		"languageProfileId": nil,
		"tags":              nil,
		"modifiedBy":        nil,
		"media":             map[string]any{"tmdbId": 100},
		"seasons":           sList,
		"requestedBy":       map[string]any{"id": 5, "displayName": "Nando <3"},
	}
	for k, v := range extra {
		req[k] = v
	}
	w.requests[id] = req
	return req
}

func (w *fakeWorld) callsBy(method string) []map[string]any {
	w.mu.Lock()
	defer w.mu.Unlock()
	var out []map[string]any
	for _, c := range w.calls {
		if c.method == method {
			out = append(out, c.params)
		}
	}
	return out
}

func (w *fakeWorld) clearCalls() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.calls = nil
}

func (w *fakeWorld) press(t *testing.T, b *Bot, data string, chat int64, message int) {
	t.Helper()
	w.mu.Lock()
	w.updateID++
	uid := w.updateID
	w.updates = append(w.updates, map[string]any{
		"update_id": uid,
		"callback_query": map[string]any{
			"id":   strconv.Itoa(uid),
			"data": data,
			"message": map[string]any{
				"message_id": message,
				"chat":       map[string]any{"id": chat},
				"caption":    "old",
			},
		},
	})
	w.calls = nil
	w.mu.Unlock()
	if err := b.Listen(context.Background()); err != nil {
		t.Fatalf("Listen failed: %v", err)
	}
}

func keyboardOf(params map[string]any) [][]map[string]any {
	rm := params["reply_markup"].(map[string]any)
	rawRows := rm["inline_keyboard"].([]any)
	rows := make([][]map[string]any, len(rawRows))
	for i, rr := range rawRows {
		btns := rr.([]any)
		rows[i] = make([]map[string]any, len(btns))
		for j, b := range btns {
			rows[i][j] = b.(map[string]any)
		}
	}
	return rows
}

func TestFirstLookSendsOnlyWhatWaits(t *testing.T) {
	w, _, b := newFakeWorld(t)
	w.add(1, "movie", StatusApproved, nil, nil)
	w.add(2, "tv", StatusPending, []int{1, 2, 3, 4, 5, 6}, nil)

	if err := b.Look(context.Background()); err != nil {
		t.Fatal(err)
	}
	sent := w.callsBy("sendPhoto")
	if len(sent) != 1 {
		t.Fatalf("expected 1 photo, got %d", len(sent))
	}
	cap := sent[0]["caption"].(string)
	if !strings.Contains(cap, "<b>Nando &lt;3</b> requested <b>The Bear</b> (2022)") {
		t.Fatalf("unexpected caption: %s", cap)
	}
	if !strings.Contains(cap, "Seasons 1–6 of 6") {
		t.Fatalf("expected seasons summary in caption: %s", cap)
	}
	keys := keyboardOf(sent[0])
	if keys[0][0]["callback_data"] != "a:2" || keys[0][1]["callback_data"] != "d:2" {
		t.Fatalf("unexpected action buttons: %v", keys[0])
	}
	if keys[1][0]["callback_data"] != "p:2:0" {
		t.Fatalf("unexpected picker button: %v", keys[1])
	}
	if keys[2][0]["url"] != "https://requests.example/tv/100" {
		t.Fatalf("unexpected url button: %v", keys[2])
	}

	w.clearCalls()
	if err := b.Look(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(w.callsBy("sendPhoto")) != 0 {
		t.Fatal("expected nothing new on second look")
	}
}

func TestNewRequestDeclined(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.add(3, "movie", StatusPending, nil, nil)
	_ = b.Look(context.Background())

	if !strings.Contains(w.callsBy("sendPhoto")[0]["caption"].(string), "Dune &amp; Co</b> (2024)") {
		t.Fatal("missing escaped movie title")
	}
	w.press(t, b, "d:3", -42, 1)
	if w.requests[3]["status"] != StatusDeclined {
		t.Fatalf("expected declined, got %v", w.requests[3]["status"])
	}
	edit := w.callsBy("editMessageCaption")[0]
	if !strings.HasSuffix(edit["caption"].(string), "❌ Declined by aurea") {
		t.Fatalf("unexpected edit caption: %s", edit["caption"])
	}
	if keyboardOf(edit)[0][0]["url"] != "https://requests.example/movie/100" {
		t.Fatal("expected Seerr link preserved")
	}
	if _, ok := b.state.Sent["3"]; ok {
		t.Fatal("expected request 3 removed from sent map")
	}
}

func TestChoosingSeasons(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.add(2, "tv", StatusPending, []int{1, 2, 3, 4, 5, 6}, nil)
	_ = b.Look(context.Background())

	w.press(t, b, "p:2:0", -42, 1)
	rows := keyboardOf(w.callsBy("editMessageReplyMarkup")[0])
	if rows[0][0]["text"] != "◻️ S1" || rows[0][0]["callback_data"] != "p:2:1" {
		t.Fatalf("unexpected first season toggle: %v", rows[0][0])
	}
	if rows[len(rows)-1][0]["text"] != "Choose a season" {
		t.Fatalf("unexpected confirm text: %v", rows[len(rows)-1][0])
	}

	w.press(t, b, "p:2:1", -42, 1)
	rows = keyboardOf(w.callsBy("editMessageReplyMarkup")[0])
	if rows[0][0]["text"] != "✅ S1" || rows[0][1]["text"] != "◻️ S2" {
		t.Fatalf("unexpected toggled buttons: %v", rows[0])
	}
	if rows[len(rows)-1][0]["text"] != "✅ Approve 1 season" || rows[len(rows)-1][0]["callback_data"] != "k:2:1" {
		t.Fatalf("unexpected approve button: %v", rows[len(rows)-1][0])
	}

	w.press(t, b, "k:2:1", -42, 1)
	if len(w.put) != 1 {
		t.Fatalf("expected 1 PUT, got %d", len(w.put))
	}
	want := map[string]any{"mediaType": "tv", "seasons": []any{float64(1)}, "is4k": false}
	if !reflect.DeepEqual(w.put[0], want) {
		t.Fatalf("PUT body mismatch:\ngot:  %#v\nwant: %#v", w.put[0], want)
	}
	if w.requests[2]["status"] != StatusApproved {
		t.Fatal("expected approved")
	}
	if !strings.HasSuffix(w.callsBy("editMessageCaption")[0]["caption"].(string), "✅ Approved by aurea: season 1") {
		t.Fatalf("unexpected caption: %s", w.callsBy("editMessageCaption")[0]["caption"])
	}
}

func TestChosenSeasonsKeepServerFields(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.add(2, "tv", StatusPending, []int{4, 5, 6}, map[string]any{
		"serverId": 0, "profileId": 7, "rootFolder": "/tv",
		"languageProfileId": 1, "tags": []int{3},
	})
	_ = b.Look(context.Background())

	w.press(t, b, "k:2:5", -42, 1)
	want := map[string]any{
		"mediaType": "tv", "seasons": []any{float64(4), float64(6)}, "is4k": false,
		"serverId": float64(0), "profileId": float64(7), "rootFolder": "/tv",
		"languageProfileId": float64(1), "tags": []any{float64(3)},
	}
	if !reflect.DeepEqual(w.put[0], want) {
		t.Fatalf("PUT body mismatch:\ngot:  %#v\nwant: %#v", w.put[0], want)
	}
	if !strings.HasSuffix(w.callsBy("editMessageCaption")[0]["caption"].(string), "✅ Approved by aurea: seasons 4, 6") {
		t.Fatalf("unexpected caption: %s", w.callsBy("editMessageCaption")[0]["caption"])
	}
}

func TestNoSeasonChosen(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.add(2, "tv", StatusPending, []int{1, 2}, nil)
	_ = b.Look(context.Background())

	w.press(t, b, "k:2:0", -42, 1)
	if w.callsBy("answerCallbackQuery")[0]["text"] != "Choose a season first" {
		t.Fatal("expected prompt to choose season")
	}
	if w.requests[2]["status"] != StatusPending {
		t.Fatal("status should stay pending")
	}
}

func TestDecidedInSeerr(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	r := w.add(4, "movie", StatusPending, nil, nil)
	_ = b.Look(context.Background())

	r["status"] = StatusApproved
	r["modifiedBy"] = map[string]any{"id": 1, "displayName": "aurea"}
	w.clearCalls()
	_ = b.Look(context.Background())
	if !strings.HasSuffix(w.callsBy("editMessageCaption")[0]["caption"].(string), "✅ Approved by aurea") {
		t.Fatal("expected caption updated")
	}
	w.press(t, b, "d:4", -42, 1)
	if r["status"] != StatusApproved {
		t.Fatal("status should not change")
	}
	if w.callsBy("answerCallbackQuery")[0]["text"] != "It was decided in Seerr already" {
		t.Fatal("expected already-decided message")
	}
}

func TestOtherChatsButtonsDoNothing(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.add(5, "movie", StatusPending, nil, nil)
	_ = b.Look(context.Background())

	w.press(t, b, "a:5", 99, 1)
	if w.requests[5]["status"] != StatusPending {
		t.Fatal("other chat should not approve")
	}
}

func TestPosterFailingFallsBackToMessage(t *testing.T) {
	w, _, b := newFakeWorld(t)
	_ = b.Look(context.Background())
	w.photoFails = true
	w.add(6, "movie", StatusPending, nil, nil)
	_ = b.Look(context.Background())

	if len(w.callsBy("sendMessage")) != 1 {
		t.Fatal("expected fallback to sendMessage")
	}
	if b.state.Sent["6"].Photo {
		t.Fatal("expected Photo=false")
	}
}

func TestRestartKeepsPlace(t *testing.T) {
	w, _, b := newFakeWorld(t)
	w.add(2, "tv", StatusPending, []int{1}, nil)
	_ = b.Look(context.Background())

	again := NewBot(b.seerr, b.telegram, "-42", "", b.statePath)
	w.clearCalls()
	if err := again.Look(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(w.callsBy("sendPhoto")) != 0 {
		t.Fatal("should not re-send after restart")
	}
	if _, ok := again.state.Sent["2"]; !ok {
		t.Fatal("should retain pending request in state")
	}
}

func TestNoOpLookSkipsDiskWrite(t *testing.T) {
	w, _, b := newFakeWorld(t)
	w.add(1, "movie", StatusApproved, nil, nil)
	if err := b.Look(context.Background()); err != nil {
		t.Fatal(err)
	}
	// Stamp an older mtime, then run another no-op Look and ensure the file is untouched.
	info1, err := os.Stat(b.statePath)
	if err != nil {
		t.Fatal(err)
	}
	older := info1.ModTime().Add(-60e9)
	if err := os.Chtimes(b.statePath, older, older); err != nil {
		t.Fatal(err)
	}
	if err := b.Look(context.Background()); err != nil {
		t.Fatal(err)
	}
	info2, err := os.Stat(b.statePath)
	if err != nil {
		t.Fatal(err)
	}
	if !info2.ModTime().Equal(older) {
		t.Fatalf("expected state.json not to be rewritten on no-op Look: mtime changed %v -> %v", older, info2.ModTime())
	}
}

func TestRangesAndEnvFile(t *testing.T) {
	if got := ranges([]int{1, 2, 3, 5, 7, 8}); got != "1–3, 5, 7–8" {
		t.Fatalf("ranges mismatch: %q", got)
	}
	if got := ranges([]int{4}); got != "4" {
		t.Fatalf("single range mismatch: %q", got)
	}

	p := filepath.Join(t.TempDir(), "env")
	if err := os.WriteFile(p, []byte("export TELEGRAM_BOT_TOKEN='1:abc'\n# a comment\nTELEGRAM_CHAT_ID=\"-42\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := readEnvFile(p)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"TELEGRAM_BOT_TOKEN": "1:abc", "TELEGRAM_CHAT_ID": "-42"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("env mismatch: got %#v, want %#v", got, want)
	}
}
