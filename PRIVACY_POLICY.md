# Privacy Policy for Product Scout

**Effective Date:** March 19, 2026

Product Scout ("we", "our", or "us") provides an AI-powered search engine for Shopify merchants. This Privacy Policy describes how we collect, use, and disclose information when you install and use our application.

## 1. Information We Collect

When you install Product Scout, we automatically collect certain information from your Shopify account to provide our services:
*   **Shop Information**: Your shop's URL, name, and owner email.
*   **Product Data**: Product titles, descriptions, types, tags, and handles. We use this to build a searchable AI index of your catalog.
*   **Search Telemetry**: We collect anonymized data on search queries made through the overlay (e.g., query text, click-through rates, and latency) to improve search accuracy for your store.

## 2. How We Use Your Information

We use the collected information to:
*   **Create AI Embeddings**: Transform your product catalog into vector representations for semantic search.
*   **Generate AI Reasoning**: Use Large Language Models (LLMs) to provide "Why it fits" justifications for search results.
*   **Analytics**: Provide you with insights into what your customers are searching for.
*   **Service Maintenance**: Monitor the health and performance of the search engine.

## 3. Data Processing and Third Parties

To provide AI features, we utilize the following sub-processors:
*   **Google Cloud (Gemini)**: We send product text and search queries to Google Gemini to generate embeddings and search reasoning. **Google does not use this data to train its foundation models.**
*   **Pinecone**: We store your product's vector embeddings in a secure, isolated namespace on Pinecone's vector database.
*   **Supabase**: We store merchant credentials (access tokens) and search telemetry in a secure PostgreSQL database on Supabase.

## 4. Data Security

We take the security of your data seriously.
*   All data transmitted between Shopify, our servers, and our sub-processors is encrypted via SSL/TLS.
*   Merchant access tokens are stored securely and used only for catalog synchronization.
*   Search data is isolated by `shop_url` to ensure multi-tenant security.

## 5. Data Retention and Deletion

We comply with Shopify's mandatory privacy requirements:
*   **App Uninstallation**: If you uninstall the app, we immediately stop syncing your data. We purge your product catalog from our vector index within 48 hours.
*   **GDPR / CCPA Requests**: We support "Request Customer Data" and "Delete Customer Data" webhooks. Since our app primarily indexes product data (and not personally identifiable information of your customers), these requests usually involve deleting search telemetry associated with your store.

## 6. Your Rights

Depending on your location, you may have rights regarding your data, including the right to access, correct, or delete the information we hold. To exercise these rights, please contact us at the email below.

## 7. Contact Us

If you have questions about this Privacy Policy, please contact us at:
[Your Support Email Here]
