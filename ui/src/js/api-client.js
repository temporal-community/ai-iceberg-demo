// Research API Client
// Handles communication with FastAPI backend (session-cookie auth)

class ResearchClient {
  constructor(baseUrl = "") {
    // Use same-origin by default. If you set baseUrl to another origin,
    // your backend must allow that origin + credentials in CORS.
    this.baseUrl = (baseUrl || "").replace(/\/$/, "");
    this.workflowId = null;
    this.eventSource = null;
  }

  async _fetch(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const response = await fetch(url, {
      credentials: "include",
      ...options,
    });

    if (response.status === 401) {
      window.location.href = "/auth/login";
      throw new Error("Not authenticated");
    }

    return response;
  }

  async startResearch(query) {
    const response = await this._fetch(`/api/start-research`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!response.ok) throw new Error("Failed to start research");

    const data = await response.json();
    this.workflowId = data.workflow_id;
    return data;
  }

  async getStatus(workflowId = null) {
    const id = workflowId || this.workflowId;
    if (!id) throw new Error("No workflow ID available");

    const response = await this._fetch(`/api/status/${id}`);
    if (!response.ok) throw new Error("Failed to get status");
    return await response.json();
  }

  async submitAnswer(answer, workflowId = null, currentQuestionIndex = 0) {
    const id = workflowId || this.workflowId;
    if (!id) throw new Error("No workflow ID available");

    const response = await this._fetch(
      `/api/answer/${id}/${currentQuestionIndex}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
      },
    );

    if (!response.ok) throw new Error("Failed to submit answer");
    return await response.json();
  }

  async getResult(workflowId = null) {
    const id = workflowId || this.workflowId;
    if (!id) throw new Error("No workflow ID available");

    const response = await this._fetch(`/api/result/${id}`);
    if (!response.ok) throw new Error("Result not ready or failed");
    return await response.json();
  }

  async listConversations(limit = 50, offset = 0) {
    const response = await this._fetch(
      `/api/conversations?limit=${limit}&offset=${offset}`,
    );
    if (!response.ok) throw new Error("Failed to list conversations");
    return await response.json();
  }

  async getConversation(workflowId) {
    const response = await this._fetch(`/api/conversations/${workflowId}`);
    if (!response.ok) throw new Error("Failed to get conversation");
    return await response.json();
  }

  async getConversationMessages(workflowId) {
    const response = await this._fetch(
      `/api/conversations/${workflowId}/messages`,
    );
    if (!response.ok) throw new Error("Failed to get conversation messages");
    return await response.json();
  }

  // ---- Personalization / Google connected account ----
  async googleStatus() {
    const response = await this._fetch(`/api/google/status`);
    if (!response.ok) throw new Error("Failed to get Google connection status");
    return await response.json(); // { connected: boolean }
  }

  async getPersonalization() {
    const response = await this._fetch(`/api/personalization`);
    if (!response.ok) throw new Error("Failed to get personalization state");
    return await response.json();
  }

  async updatePersonalizationFromGoogleDoc(driveFileId) {
    const response = await this._fetch(
      `/api/personalization/update-from-google-doc`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ drive_file_id: driveFileId }),
      },
    );

    if (response.status === 403) {
      window.location.href = "/auth/connect?connection=google-oauth2";
      throw new Error("Google not connected");
    }

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(
        `Failed to update personalization (${response.status}): ${text}`,
      );
    }

    return await response.json();
  }

  // Server-Sent Events for live updates (currently backend returns 501)
  streamStatus(workflowId, onUpdate, onComplete, onError) {
    const id = workflowId || this.workflowId;
    if (!id) throw new Error("No workflow ID available");

    this.eventSource = new EventSource(`${this.baseUrl}/api/stream/${id}`);

    this.eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      onUpdate(data);

      if (data.status === "complete") {
        this.closeStream();
        if (onComplete) onComplete(data);
      }
    };

    this.eventSource.onerror = (error) => {
      console.error("Stream error:", error);
      this.closeStream();
      if (onError) onError(error);
    };
  }

  closeStream() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  async pollStatus(workflowId, onUpdate, interval = 2000) {
    const id = workflowId || this.workflowId;

    const poll = async () => {
      try {
        const status = await this.getStatus(id);
        onUpdate(status);

        if (status.status !== "complete" && status.status !== "failed") {
          setTimeout(poll, interval);
        }
      } catch (error) {
        console.error("Polling error:", error);
      }
    };

    poll();
  }
}

if (typeof window !== "undefined") {
  window.ResearchClient = ResearchClient;
}
