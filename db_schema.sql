--
-- PostgreSQL database dump
--

\restrict H37t2g55Mbayl0YwbsESgotZuB5p041Plmk2Nn23aqngDE1xGWDnw9Vt96p8zc6

-- Dumped from database version 17.6 (Ubuntu 17.6-1.pgdg24.04+1)
-- Dumped by pg_dump version 17.6 (Ubuntu 17.6-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ad_groups; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ad_groups (
    id integer NOT NULL,
    group_id bigint NOT NULL,
    status smallint DEFAULT 1 NOT NULL,
    end_date date NOT NULL,
    creator_id bigint NOT NULL
);


ALTER TABLE public.ad_groups OWNER TO postgres;

--
-- Name: ad_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.ad_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ad_groups_id_seq OWNER TO postgres;

--
-- Name: ad_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.ad_groups_id_seq OWNED BY public.ad_groups.id;


--
-- Name: events_counter; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.events_counter (
    id integer NOT NULL,
    type smallint NOT NULL,
    event_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.events_counter OWNER TO postgres;

--
-- Name: events_counter_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.events_counter_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.events_counter_id_seq OWNER TO postgres;

--
-- Name: events_counter_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.events_counter_id_seq OWNED BY public.events_counter.id;


--
-- Name: manual_payments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.manual_payments (
    id integer NOT NULL,
    payment_state jsonb NOT NULL
);


ALTER TABLE public.manual_payments OWNER TO postgres;

--
-- Name: manual_payments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.manual_payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.manual_payments_id_seq OWNER TO postgres;

--
-- Name: manual_payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.manual_payments_id_seq OWNED BY public.manual_payments.id;


--
-- Name: newsletters; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.newsletters (
    id integer NOT NULL,
    creation_time timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    creator_id bigint NOT NULL,
    text text NOT NULL,
    expires_at date,
    target smallint,
    send_time time without time zone,
    is_moderating boolean,
    file_id text
);


ALTER TABLE public.newsletters OWNER TO postgres;

--
-- Name: newsletters_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.newsletters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.newsletters_id_seq OWNER TO postgres;

--
-- Name: newsletters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.newsletters_id_seq OWNED BY public.newsletters.id;


--
-- Name: partner_groups; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.partner_groups (
    id integer NOT NULL,
    group_id bigint NOT NULL,
    show_ad_ids integer[] DEFAULT '{}'::integer[],
    need_groups bigint[] DEFAULT '{}'::bigint[],
    partner_type smallint DEFAULT 1,
    creator_id bigint,
    region_codes smallint[],
    poster_categories smallint[]
);


ALTER TABLE public.partner_groups OWNER TO postgres;

--
-- Name: partner_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.partner_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.partner_groups_id_seq OWNER TO postgres;

--
-- Name: partner_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.partner_groups_id_seq OWNED BY public.partner_groups.id;


--
-- Name: partners; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.partners (
    id integer NOT NULL,
    user_id bigint NOT NULL,
    balance integer DEFAULT 0
);


ALTER TABLE public.partners OWNER TO postgres;

--
-- Name: partners_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.partners_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.partners_id_seq OWNER TO postgres;

--
-- Name: partners_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.partners_id_seq OWNED BY public.partners.id;


--
-- Name: payments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.payments (
    id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    type smallint NOT NULL,
    from_user bigint NOT NULL,
    to_user bigint NOT NULL,
    sum integer NOT NULL
);


ALTER TABLE public.payments OWNER TO postgres;

--
-- Name: payments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.payments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payments_id_seq OWNER TO postgres;

--
-- Name: payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.payments_id_seq OWNED BY public.payments.id;


--
-- Name: posters; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.posters (
    id integer NOT NULL,
    file_id text,
    text text NOT NULL,
    status smallint DEFAULT 0 NOT NULL,
    creator_id bigint NOT NULL,
    end_date date NOT NULL,
    region_codes smallint[],
    topic_id smallint,
    referral_button_name text
);


ALTER TABLE public.posters OWNER TO postgres;

--
-- Name: posters_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.posters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.posters_id_seq OWNER TO postgres;

--
-- Name: posters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.posters_id_seq OWNED BY public.posters.id;


--
-- Name: queue; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.queue (
    id integer NOT NULL,
    activate_time timestamp without time zone NOT NULL,
    group_id bigint NOT NULL,
    poster_id integer NOT NULL
);


ALTER TABLE public.queue OWNER TO postgres;

--
-- Name: quene_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.quene_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.quene_id_seq OWNER TO postgres;

--
-- Name: quene_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.quene_id_seq OWNED BY public.queue.id;


--
-- Name: tg_groups; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tg_groups (
    id integer NOT NULL,
    group_id bigint NOT NULL,
    sub_type character(50),
    need_groups_id bigint[],
    rates jsonb,
    rate_type character(40)
);


ALTER TABLE public.tg_groups OWNER TO postgres;

--
-- Name: tg_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.tg_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tg_groups_id_seq OWNER TO postgres;

--
-- Name: tg_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.tg_groups_id_seq OWNED BY public.tg_groups.id;


--
-- Name: user_requests; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_requests (
    id integer NOT NULL,
    type smallint NOT NULL,
    user_id bigint NOT NULL,
    comment text,
    amount integer,
    status smallint
);


ALTER TABLE public.user_requests OWNER TO postgres;

--
-- Name: user_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.user_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_requests_id_seq OWNER TO postgres;

--
-- Name: user_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.user_requests_id_seq OWNED BY public.user_requests.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    id integer NOT NULL,
    user_id bigint NOT NULL,
    status character(3),
    referral_user_id bigint
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: users_subs_info; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users_subs_info (
    id integer NOT NULL,
    user_id bigint NOT NULL,
    group_id bigint NOT NULL,
    type character(50) NOT NULL,
    expires_at timestamp without time zone,
    msg_left integer
);


ALTER TABLE public.users_subs_info OWNER TO postgres;

--
-- Name: users_subs_info_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

ALTER TABLE public.users_subs_info ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.users_subs_info_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ad_groups id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ad_groups ALTER COLUMN id SET DEFAULT nextval('public.ad_groups_id_seq'::regclass);


--
-- Name: events_counter id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.events_counter ALTER COLUMN id SET DEFAULT nextval('public.events_counter_id_seq'::regclass);


--
-- Name: manual_payments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manual_payments ALTER COLUMN id SET DEFAULT nextval('public.manual_payments_id_seq'::regclass);


--
-- Name: newsletters id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.newsletters ALTER COLUMN id SET DEFAULT nextval('public.newsletters_id_seq'::regclass);


--
-- Name: partner_groups id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.partner_groups ALTER COLUMN id SET DEFAULT nextval('public.partner_groups_id_seq'::regclass);


--
-- Name: partners id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.partners ALTER COLUMN id SET DEFAULT nextval('public.partners_id_seq'::regclass);


--
-- Name: payments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments ALTER COLUMN id SET DEFAULT nextval('public.payments_id_seq'::regclass);


--
-- Name: posters id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posters ALTER COLUMN id SET DEFAULT nextval('public.posters_id_seq'::regclass);


--
-- Name: queue id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue ALTER COLUMN id SET DEFAULT nextval('public.quene_id_seq'::regclass);


--
-- Name: tg_groups id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tg_groups ALTER COLUMN id SET DEFAULT nextval('public.tg_groups_id_seq'::regclass);


--
-- Name: user_requests id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_requests ALTER COLUMN id SET DEFAULT nextval('public.user_requests_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: ad_groups ad_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ad_groups
    ADD CONSTRAINT ad_groups_pkey PRIMARY KEY (id);


--
-- Name: events_counter events_counter_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.events_counter
    ADD CONSTRAINT events_counter_pkey PRIMARY KEY (id);


--
-- Name: manual_payments manual_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.manual_payments
    ADD CONSTRAINT manual_payments_pkey PRIMARY KEY (id);


--
-- Name: newsletters newsletters_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.newsletters
    ADD CONSTRAINT newsletters_pkey PRIMARY KEY (id);


--
-- Name: partner_groups partner_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.partner_groups
    ADD CONSTRAINT partner_groups_pkey PRIMARY KEY (id);


--
-- Name: partners partners_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.partners
    ADD CONSTRAINT partners_pkey PRIMARY KEY (id);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: posters posters_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posters
    ADD CONSTRAINT posters_pkey PRIMARY KEY (id);


--
-- Name: queue quene_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue
    ADD CONSTRAINT quene_pkey PRIMARY KEY (id);


--
-- Name: tg_groups tg_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tg_groups
    ADD CONSTRAINT tg_groups_pkey PRIMARY KEY (id);


--
-- Name: user_requests user_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_requests
    ADD CONSTRAINT user_requests_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users_subs_info users_subs_info_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users_subs_info
    ADD CONSTRAINT users_subs_info_pkey PRIMARY KEY (id);


--
-- PostgreSQL database dump complete
--

\unrestrict H37t2g55Mbayl0YwbsESgotZuB5p041Plmk2Nn23aqngDE1xGWDnw9Vt96p8zc6

