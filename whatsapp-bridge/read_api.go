package main

// Authenticated, read-only WhatsApp account queries for MCP and other bridge clients.
// Query names select fixed SQL; callers cannot submit SQL text.
import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strings"
)

type readRequest struct {
	Kind               string `json:"kind"`
	ChatJID            string `json:"chat_jid"`
	Sender             string `json:"sender"`
	Query              string `json:"query"`
	MessageID          string `json:"message_id"`
	After              string `json:"after"`
	Before             string `json:"before"`
	Status             string `json:"status"`
	Limit              int    `json:"limit"`
	Page               int    `json:"page"`
	ContextBefore      int    `json:"context_before"`
	ContextAfter       int    `json:"context_after"`
	SortBy             string `json:"sort_by"`
	IncludeLastMessage bool   `json:"include_last_message"`
}

func registerReadAPI(store *MessageStore) {
	http.HandleFunc("/api/read", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var q readRequest
		if err := json.NewDecoder(r.Body).Decode(&q); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		if q.Limit <= 0 {
			q.Limit = 20
		}
		if q.Limit > 500 {
			q.Limit = 500
		}
		if q.Page < 0 {
			q.Page = 0
		}
		rows, err := executeRead(store.db, q)
		if err != nil {
			http.Error(w, "Read query failed", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"success": true, "rows": rows})
	})
}

func executeRead(db *sql.DB, q readRequest) ([]map[string]any, error) {
	where, args := []string{}, []any{}
	add := func(expr string, value any) { where = append(where, expr); args = append(args, value) }
	limit := q.Limit
	offset := q.Page * limit
	var sqlText string
	switch q.Kind {
	case "messages", "search":
		if q.ChatJID != "" {
			add("m.chat_jid = ?", q.ChatJID)
		}
		if q.Sender != "" {
			add("m.sender = ?", q.Sender)
		}
		if q.After != "" {
			add("m.timestamp > ?", q.After)
		}
		if q.Before != "" {
			add("m.timestamp < ?", q.Before)
		}
		if q.Query != "" {
			add("LOWER(m.content) LIKE LOWER(?)", "%"+q.Query+"%")
		}
		sqlText = `SELECT m.timestamp, m.sender, c.name AS chat_name, m.content, m.is_from_me, m.chat_jid, m.id, m.media_type
			FROM messages m JOIN chats c ON c.jid=m.chat_jid`
		if len(where) > 0 {
			sqlText += " WHERE " + strings.Join(where, " AND ")
		}
		sqlText += " ORDER BY m.timestamp DESC LIMIT ? OFFSET ?"
		args = append(args, limit, offset)
	case "context":
		var target map[string]any
		r, err := db.Query(`SELECT m.timestamp,m.sender,c.name AS chat_name,m.content,m.is_from_me,m.chat_jid,m.id,m.media_type FROM messages m JOIN chats c ON c.jid=m.chat_jid WHERE m.id=? LIMIT 1`, q.MessageID)
		if err != nil {
			return nil, err
		}
		targetRows, err := readRows(r)
		if err != nil {
			return nil, err
		}
		if len(targetRows) == 0 {
			return []map[string]any{}, nil
		}
		target = targetRows[0]
		chat, ts := target["chat_jid"], target["timestamp"]
		before := q.ContextBefore
		after := q.ContextAfter
		if before < 0 {
			before = 0
		}
		if after < 0 {
			after = 0
		}
		if before > 100 {
			before = 100
		}
		if after > 100 {
			after = 100
		}
		b, err := db.Query(`SELECT m.timestamp,m.sender,c.name AS chat_name,m.content,m.is_from_me,m.chat_jid,m.id,m.media_type FROM messages m JOIN chats c ON c.jid=m.chat_jid WHERE m.chat_jid=? AND m.timestamp<? ORDER BY m.timestamp DESC LIMIT ?`, chat, ts, before)
		if err != nil {
			return nil, err
		}
		br, err := readRows(b)
		if err != nil {
			return nil, err
		}
		a, err := db.Query(`SELECT m.timestamp,m.sender,c.name AS chat_name,m.content,m.is_from_me,m.chat_jid,m.id,m.media_type FROM messages m JOIN chats c ON c.jid=m.chat_jid WHERE m.chat_jid=? AND m.timestamp>? ORDER BY m.timestamp ASC LIMIT ?`, chat, ts, after)
		if err != nil {
			return nil, err
		}
		ar, err := readRows(a)
		if err != nil {
			return nil, err
		}
		out := append(br, target)
		out = append(out, ar...)
		return out, nil
	case "chats":
		if q.Query != "" {
			add("(LOWER(c.name) LIKE LOWER(?) OR c.jid LIKE ?)", "%"+q.Query+"%")
			args = append(args, "%"+q.Query+"%")
		}
		order := "c.last_message_time DESC"
		if q.SortBy == "name" {
			order = "c.name"
		}
		sqlText = `SELECT c.jid,c.name,c.last_message_time,m.content AS last_message,m.sender AS last_sender,m.is_from_me AS last_is_from_me FROM chats c LEFT JOIN messages m ON c.jid=m.chat_jid AND c.last_message_time=m.timestamp`
		if len(where) > 0 {
			sqlText += " WHERE " + strings.Join(where, " AND ")
		}
		sqlText += " ORDER BY " + order + " LIMIT ? OFFSET ?"
		args = append(args, limit, offset)
	case "chat":
		sqlText = `SELECT c.jid,c.name,c.last_message_time,m.content AS last_message,m.sender AS last_sender,m.is_from_me AS last_is_from_me FROM chats c LEFT JOIN messages m ON c.jid=m.chat_jid AND c.last_message_time=m.timestamp WHERE c.jid=? LIMIT 1`
		args = []any{q.ChatJID}
	case "contacts":
		sqlText = `SELECT DISTINCT jid,name FROM chats WHERE (LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?)) AND jid NOT LIKE '%@g.us' ORDER BY name,jid LIMIT 50`
		args = []any{"%" + q.Query + "%", "%" + q.Query + "%"}
	case "contact_chats", "last_interaction":
		add("m.sender = ? OR c.jid = ?", q.Sender)
		args = append(args, q.Sender)
		sqlText = `SELECT DISTINCT c.jid,c.name,c.last_message_time,m.content AS last_message,m.sender AS last_sender,m.is_from_me AS last_is_from_me FROM chats c JOIN messages m ON c.jid=m.chat_jid WHERE ` + where[0] + ` ORDER BY c.last_message_time DESC LIMIT ? OFFSET ?`
		args = append(args, limit, offset)
	case "direct_chat":
		sqlText = `SELECT c.jid,c.name,c.last_message_time,m.content AS last_message,m.sender AS last_sender,m.is_from_me AS last_is_from_me FROM chats c LEFT JOIN messages m ON c.jid=m.chat_jid AND c.last_message_time=m.timestamp WHERE c.jid LIKE ? AND c.jid NOT LIKE '%@g.us' LIMIT 1`
		args = []any{"%" + q.Sender + "%"}
	case "polls":
		if q.ChatJID != "" {
			add("chat_jid=?", q.ChatJID)
		}
		sqlText = `SELECT poll_id,chat_jid,creator,question,options,timestamp FROM polls`
		if len(where) > 0 {
			sqlText += " WHERE " + strings.Join(where, " AND ")
		}
		sqlText += " ORDER BY timestamp DESC LIMIT ?"
		args = append(args, limit)
	case "poll_votes":
		sqlText = `SELECT voter,options,timestamp FROM poll_votes WHERE poll_id=? AND chat_jid=?`
		args = []any{q.MessageID, q.ChatJID}
	case "drafts":
		if q.Status == "" {
			q.Status = "pending"
		}
		sqlText = `SELECT id,recipient,name,message,created,status FROM drafts WHERE status=? ORDER BY id DESC LIMIT ?`
		args = []any{q.Status, limit}
	default:
		return nil, http.ErrNotSupported
	}
	r, err := db.Query(sqlText, args...)
	if err != nil {
		return nil, err
	}
	return readRows(r)
}

func readRows(rows *sql.Rows) ([]map[string]any, error) {
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptr := make([]any, len(cols))
		for i := range vals {
			ptr[i] = &vals[i]
		}
		if err := rows.Scan(ptr...); err != nil {
			return nil, err
		}
		row := map[string]any{}
		for i, c := range cols {
			if b, ok := vals[i].([]byte); ok {
				row[c] = string(b)
			} else {
				row[c] = vals[i]
			}
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
