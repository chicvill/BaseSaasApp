import e, { PureComponent as t, createRef as n } from "react";
//#region src/utils.ts
var r = {
	x: 0,
	y: 0,
	width: 0,
	height: 0,
	unit: "px"
}, i = (e, t, n) => Math.min(Math.max(e, t), n), a = (...e) => e.filter((e) => e && typeof e == "string").join(" "), o = (e, t) => e === t || e.width === t.width && e.height === t.height && e.x === t.x && e.y === t.y && e.unit === t.unit;
function s(e, t, n, r) {
	let i = u(e, n, r);
	return e.width && (i.height = i.width / t), e.height && (i.width = i.height * t), i.y + i.height > r && (i.height = r - i.y, i.width = i.height * t), i.x + i.width > n && (i.width = n - i.x, i.height = i.width / t), e.unit === "%" ? l(i, n, r) : i;
}
function c(e, t, n) {
	let r = u(e, t, n);
	return r.x = (t - r.width) / 2, r.y = (n - r.height) / 2, e.unit === "%" ? l(r, t, n) : r;
}
function l(e, t, n) {
	return e.unit === "%" ? {
		...r,
		...e,
		unit: "%"
	} : {
		unit: "%",
		x: e.x ? e.x / t * 100 : 0,
		y: e.y ? e.y / n * 100 : 0,
		width: e.width ? e.width / t * 100 : 0,
		height: e.height ? e.height / n * 100 : 0
	};
}
function u(e, t, n) {
	return !e.unit || e.unit === "px" ? {
		...r,
		...e,
		unit: "px"
	} : {
		unit: "px",
		x: e.x ? e.x * t / 100 : 0,
		y: e.y ? e.y * n / 100 : 0,
		width: e.width ? e.width * t / 100 : 0,
		height: e.height ? e.height * n / 100 : 0
	};
}
function d(e, t, n, r, i, a = 0, o = 0, s = r, c = i) {
	let l = { ...e }, u = Math.min(a, r), d = Math.min(o, i), f = Math.min(s, r), p = Math.min(c, i);
	t && (t > 1 ? (u = o ? o * t : u, d = u / t, f = s * t) : (d = a ? a / t : d, u = d * t, p = c / t)), l.y < 0 && (l.height = Math.max(l.height + l.y, d), l.y = 0), l.x < 0 && (l.width = Math.max(l.width + l.x, u), l.x = 0);
	let m = r - (l.x + l.width);
	m < 0 && (l.x = Math.min(l.x, r - u), l.width += m);
	let h = i - (l.y + l.height);
	if (h < 0 && (l.y = Math.min(l.y, i - d), l.height += h), l.width < u && ((n === "sw" || n == "nw") && (l.x -= u - l.width), l.width = u), l.height < d && ((n === "nw" || n == "ne") && (l.y -= d - l.height), l.height = d), l.width > f && ((n === "sw" || n == "nw") && (l.x -= f - l.width), l.width = f), l.height > p && ((n === "nw" || n == "ne") && (l.y -= p - l.height), l.height = p), t) {
		let e = l.width / l.height;
		if (e < t) {
			let e = Math.max(l.width / t, d);
			(n === "nw" || n == "ne") && (l.y -= e - l.height), l.height = e;
		} else if (e > t) {
			let e = Math.max(l.height * t, u);
			(n === "sw" || n == "nw") && (l.x -= e - l.width), l.width = e;
		}
	}
	return l;
}
function f(e, t, n, r) {
	let i = { ...e };
	return t === "ArrowLeft" ? r === "nw" ? (i.x -= n, i.y -= n, i.width += n, i.height += n) : r === "w" ? (i.x -= n, i.width += n) : r === "sw" ? (i.x -= n, i.width += n, i.height += n) : r === "ne" ? (i.y += n, i.width -= n, i.height -= n) : r === "e" ? i.width -= n : r === "se" && (i.width -= n, i.height -= n) : t === "ArrowRight" && (r === "nw" ? (i.x += n, i.y += n, i.width -= n, i.height -= n) : r === "w" ? (i.x += n, i.width -= n) : r === "sw" ? (i.x += n, i.width -= n, i.height -= n) : r === "ne" ? (i.y -= n, i.width += n, i.height += n) : r === "e" ? i.width += n : r === "se" && (i.width += n, i.height += n)), t === "ArrowUp" ? r === "nw" ? (i.x -= n, i.y -= n, i.width += n, i.height += n) : r === "n" ? (i.y -= n, i.height += n) : r === "ne" ? (i.y -= n, i.width += n, i.height += n) : r === "sw" ? (i.x += n, i.width -= n, i.height -= n) : r === "s" ? i.height -= n : r === "se" && (i.width -= n, i.height -= n) : t === "ArrowDown" && (r === "nw" ? (i.x += n, i.y += n, i.width -= n, i.height -= n) : r === "n" ? (i.y += n, i.height -= n) : r === "ne" ? (i.y += n, i.width -= n, i.height -= n) : r === "sw" ? (i.x -= n, i.width += n, i.height += n) : r === "s" ? i.height += n : r === "se" && (i.width += n, i.height += n)), i;
}
//#endregion
//#region src/ReactCrop.tsx
var p = {
	capture: !0,
	passive: !1
}, m = 0, h = class s extends t {
	static xOrds = ["e", "w"];
	static yOrds = ["n", "s"];
	static xyOrds = [
		"nw",
		"ne",
		"se",
		"sw"
	];
	static nudgeStep = 1;
	static nudgeStepMedium = 10;
	static nudgeStepLarge = 100;
	static defaultProps = { ariaLabels: {
		cropArea: "Use the arrow keys to move the crop selection area",
		nwDragHandle: "Use the arrow keys to move the north west drag handle to change the crop selection area",
		nDragHandle: "Use the up and down arrow keys to move the north drag handle to change the crop selection area",
		neDragHandle: "Use the arrow keys to move the north east drag handle to change the crop selection area",
		eDragHandle: "Use the up and down arrow keys to move the east drag handle to change the crop selection area",
		seDragHandle: "Use the arrow keys to move the south east drag handle to change the crop selection area",
		sDragHandle: "Use the up and down arrow keys to move the south drag handle to change the crop selection area",
		swDragHandle: "Use the arrow keys to move the south west drag handle to change the crop selection area",
		wDragHandle: "Use the up and down arrow keys to move the west drag handle to change the crop selection area"
	} };
	get document() {
		return document;
	}
	docMoveBound = !1;
	mouseDownOnCrop = !1;
	dragStarted = !1;
	evData = {
		startClientX: 0,
		startClientY: 0,
		startCropX: 0,
		startCropY: 0,
		clientX: 0,
		clientY: 0,
		isResize: !0
	};
	componentRef = n();
	mediaRef = n();
	resizeObserver;
	initChangeCalled = !1;
	instanceId = `rc-${m++}`;
	state = {
		cropIsActive: !1,
		newCropIsBeingDrawn: !1
	};
	getBox() {
		let e = this.mediaRef.current;
		if (!e) return {
			x: 0,
			y: 0,
			width: 0,
			height: 0
		};
		let { x: t, y: n, width: r, height: i } = e.getBoundingClientRect();
		return {
			x: t,
			y: n,
			width: r,
			height: i
		};
	}
	componentDidUpdate(e) {
		let { crop: t, onComplete: n } = this.props;
		if (n && !e.crop && t) {
			let { width: e, height: r } = this.getBox();
			e && r && n(u(t, e, r), l(t, e, r));
		}
	}
	componentWillUnmount() {
		this.resizeObserver && this.resizeObserver.disconnect(), this.unbindDocMove();
	}
	bindDocMove() {
		this.docMoveBound ||= (this.document.addEventListener("pointermove", this.onDocPointerMove, p), this.document.addEventListener("pointerup", this.onDocPointerDone, p), this.document.addEventListener("pointercancel", this.onDocPointerDone, p), !0);
	}
	unbindDocMove() {
		this.docMoveBound &&= (this.document.removeEventListener("pointermove", this.onDocPointerMove, p), this.document.removeEventListener("pointerup", this.onDocPointerDone, p), this.document.removeEventListener("pointercancel", this.onDocPointerDone, p), !1);
	}
	onCropPointerDown = (e) => {
		let { crop: t, disabled: n } = this.props, r = this.getBox();
		if (!t) return;
		let i = u(t, r.width, r.height);
		if (n) return;
		e.cancelable && e.preventDefault(), this.bindDocMove(), this.componentRef.current.focus({ preventScroll: !0 });
		let a = e.target.dataset.ord, o = !!a, s = e.clientX, c = e.clientY, l = i.x, d = i.y;
		if (a) {
			let t = e.clientX - r.x, n = e.clientY - r.y, o = 0, u = 0;
			a === "ne" || a == "e" ? (o = t - (i.x + i.width), u = n - i.y, l = i.x, d = i.y + i.height) : a === "se" || a === "s" ? (o = t - (i.x + i.width), u = n - (i.y + i.height), l = i.x, d = i.y) : a === "sw" || a == "w" ? (o = t - i.x, u = n - (i.y + i.height), l = i.x + i.width, d = i.y) : (a === "nw" || a == "n") && (o = t - i.x, u = n - i.y, l = i.x + i.width, d = i.y + i.height), s = l + r.x + o, c = d + r.y + u;
		}
		this.evData = {
			startClientX: s,
			startClientY: c,
			startCropX: l,
			startCropY: d,
			clientX: e.clientX,
			clientY: e.clientY,
			isResize: o,
			ord: a
		}, this.mouseDownOnCrop = !0, this.setState({ cropIsActive: !0 });
	};
	onComponentPointerDown = (e) => {
		let { crop: t, disabled: n, locked: r, keepSelection: i, onChange: a } = this.props, o = this.getBox();
		if (n || r || i && t) return;
		e.cancelable && e.preventDefault(), this.bindDocMove(), this.componentRef.current.focus({ preventScroll: !0 });
		let s = e.clientX - o.x, c = e.clientY - o.y, d = {
			unit: "px",
			x: s,
			y: c,
			width: 0,
			height: 0
		};
		this.evData = {
			startClientX: e.clientX,
			startClientY: e.clientY,
			startCropX: s,
			startCropY: c,
			clientX: e.clientX,
			clientY: e.clientY,
			isResize: !0
		}, this.mouseDownOnCrop = !0, a(u(d, o.width, o.height), l(d, o.width, o.height)), this.setState({
			cropIsActive: !0,
			newCropIsBeingDrawn: !0
		});
	};
	onDocPointerMove = (e) => {
		let { crop: t, disabled: n, onChange: r, onDragStart: i } = this.props, a = this.getBox();
		if (n || !t || !this.mouseDownOnCrop) return;
		e.cancelable && e.preventDefault(), this.dragStarted || (this.dragStarted = !0, i && i(e));
		let { evData: s } = this;
		s.clientX = e.clientX, s.clientY = e.clientY;
		let c;
		c = s.isResize ? this.resizeCrop() : this.dragCrop(), o(t, c) || r(u(c, a.width, a.height), l(c, a.width, a.height));
	};
	onComponentKeyDown = (e) => {
		let { crop: t, disabled: n, onChange: r, onComplete: a } = this.props;
		if (n) return;
		let o = e.key, c = !1;
		if (!t) return;
		let d = this.getBox(), f = this.makePixelCrop(d), p = (navigator.platform.match("Mac") ? e.metaKey : e.ctrlKey) ? s.nudgeStepLarge : e.shiftKey ? s.nudgeStepMedium : s.nudgeStep;
		if (o === "ArrowLeft" ? (f.x -= p, c = !0) : o === "ArrowRight" ? (f.x += p, c = !0) : o === "ArrowUp" ? (f.y -= p, c = !0) : o === "ArrowDown" && (f.y += p, c = !0), c) {
			e.cancelable && e.preventDefault(), f.x = i(f.x, 0, d.width - f.width), f.y = i(f.y, 0, d.height - f.height);
			let t = u(f, d.width, d.height), n = l(f, d.width, d.height);
			r(t, n), a && a(t, n);
		}
	};
	onHandlerKeyDown = (e, t) => {
		let { aspect: n = 0, crop: r, disabled: i, minWidth: a = 0, minHeight: c = 0, maxWidth: p, maxHeight: m, onChange: h, onComplete: g } = this.props, _ = this.getBox();
		if (i || !r) return;
		if (e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight") e.stopPropagation(), e.preventDefault();
		else return;
		let v = (navigator.platform.match("Mac") ? e.metaKey : e.ctrlKey) ? s.nudgeStepLarge : e.shiftKey ? s.nudgeStepMedium : s.nudgeStep, y = d(f(u(r, _.width, _.height), e.key, v, t), n, t, _.width, _.height, a, c, p, m);
		if (!o(r, y)) {
			let e = l(y, _.width, _.height);
			h(y, e), g && g(y, e);
		}
	};
	onDocPointerDone = (e) => {
		let { crop: t, disabled: n, onComplete: r, onDragEnd: i } = this.props, a = this.getBox();
		this.unbindDocMove(), !(n || !t) && this.mouseDownOnCrop && (this.mouseDownOnCrop = !1, this.dragStarted = !1, i && i(e), r && r(u(t, a.width, a.height), l(t, a.width, a.height)), this.setState({
			cropIsActive: !1,
			newCropIsBeingDrawn: !1
		}));
	};
	onDragFocus = () => {
		this.componentRef.current?.scrollTo(0, 0);
	};
	getCropStyle() {
		let { crop: e } = this.props;
		if (e) return {
			top: `${e.y}${e.unit}`,
			left: `${e.x}${e.unit}`,
			width: `${e.width}${e.unit}`,
			height: `${e.height}${e.unit}`
		};
	}
	dragCrop() {
		let { evData: e } = this, t = this.getBox(), n = this.makePixelCrop(t), r = e.clientX - e.startClientX, a = e.clientY - e.startClientY;
		return n.x = i(e.startCropX + r, 0, t.width - n.width), n.y = i(e.startCropY + a, 0, t.height - n.height), n;
	}
	getPointRegion(e, t, n, r) {
		let { evData: i } = this, a = i.clientX - e.x, o = i.clientY - e.y, s;
		s = r && t ? t === "nw" || t === "n" || t === "ne" : o < i.startCropY;
		let c;
		return c = n && t ? t === "nw" || t === "w" || t === "sw" : a < i.startCropX, c ? s ? "nw" : "sw" : s ? "ne" : "se";
	}
	resolveMinDimensions(e, t, n = 0, r = 0) {
		let i = Math.min(n, e.width), a = Math.min(r, e.height);
		return !t || !i && !a ? [i, a] : t > 1 ? i ? [i, i / t] : [a * t, a] : a ? [a * t, a] : [i, i / t];
	}
	resizeCrop() {
		let { evData: e } = this, { aspect: t = 0, maxWidth: n, maxHeight: r } = this.props, a = this.getBox(), [o, c] = this.resolveMinDimensions(a, t, this.props.minWidth, this.props.minHeight), l = this.makePixelCrop(a), u = this.getPointRegion(a, e.ord, o, c), f = e.ord || u, p = e.clientX - e.startClientX, m = e.clientY - e.startClientY;
		(o && f === "nw" || f === "w" || f === "sw") && (p = Math.min(p, -o)), (c && f === "nw" || f === "n" || f === "ne") && (m = Math.min(m, -c));
		let h = {
			unit: "px",
			x: 0,
			y: 0,
			width: 0,
			height: 0
		};
		u === "ne" ? (h.x = e.startCropX, h.width = p, t ? (h.height = h.width / t, h.y = e.startCropY - h.height) : (h.height = Math.abs(m), h.y = e.startCropY - h.height)) : u === "se" ? (h.x = e.startCropX, h.y = e.startCropY, h.width = p, t ? h.height = h.width / t : h.height = m) : u === "sw" ? (h.x = e.startCropX + p, h.y = e.startCropY, h.width = Math.abs(p), t ? h.height = h.width / t : h.height = m) : u === "nw" && (h.x = e.startCropX + p, h.width = Math.abs(p), t ? (h.height = h.width / t, h.y = e.startCropY - h.height) : (h.height = Math.abs(m), h.y = e.startCropY + m));
		let g = d(h, t, u, a.width, a.height, o, c, n, r);
		return t || s.xyOrds.indexOf(f) > -1 ? l = g : s.xOrds.indexOf(f) > -1 ? (l.x = g.x, l.width = g.width) : s.yOrds.indexOf(f) > -1 && (l.y = g.y, l.height = g.height), l.x = i(l.x, 0, a.width - l.width), l.y = i(l.y, 0, a.height - l.height), l;
	}
	renderCropSelection() {
		let { ariaLabels: t = s.defaultProps.ariaLabels, disabled: n, locked: r, renderSelectionAddon: i, ruleOfThirds: a, crop: o } = this.props, c = this.getCropStyle();
		if (o) return /* @__PURE__ */ e.createElement("div", {
			style: c,
			className: "ReactCrop__crop-selection",
			onPointerDown: this.onCropPointerDown,
			"aria-label": t.cropArea,
			tabIndex: 0,
			onKeyDown: this.onComponentKeyDown,
			role: "group"
		}, !n && !r && /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-elements",
			onFocus: this.onDragFocus
		}, /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-bar ord-n",
			"data-ord": "n"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-bar ord-e",
			"data-ord": "e"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-bar ord-s",
			"data-ord": "s"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-bar ord-w",
			"data-ord": "w"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-nw",
			"data-ord": "nw",
			tabIndex: 0,
			"aria-label": t.nwDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "nw"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-n",
			"data-ord": "n",
			tabIndex: 0,
			"aria-label": t.nDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "n"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-ne",
			"data-ord": "ne",
			tabIndex: 0,
			"aria-label": t.neDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "ne"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-e",
			"data-ord": "e",
			tabIndex: 0,
			"aria-label": t.eDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "e"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-se",
			"data-ord": "se",
			tabIndex: 0,
			"aria-label": t.seDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "se"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-s",
			"data-ord": "s",
			tabIndex: 0,
			"aria-label": t.sDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "s"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-sw",
			"data-ord": "sw",
			tabIndex: 0,
			"aria-label": t.swDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "sw"),
			role: "button"
		}), /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__drag-handle ord-w",
			"data-ord": "w",
			tabIndex: 0,
			"aria-label": t.wDragHandle,
			onKeyDown: (e) => this.onHandlerKeyDown(e, "w"),
			role: "button"
		})), i && /* @__PURE__ */ e.createElement("div", {
			className: "ReactCrop__selection-addon",
			onPointerDown: (e) => e.stopPropagation()
		}, i(this.state)), a && /* @__PURE__ */ e.createElement(e.Fragment, null, /* @__PURE__ */ e.createElement("div", { className: "ReactCrop__rule-of-thirds-hz" }), /* @__PURE__ */ e.createElement("div", { className: "ReactCrop__rule-of-thirds-vt" })));
	}
	makePixelCrop(e) {
		return u({
			...r,
			...this.props.crop || {}
		}, e.width, e.height);
	}
	render() {
		let { aspect: t, children: n, circularCrop: r, className: i, crop: o, disabled: s, locked: c, style: l, ruleOfThirds: u } = this.props, { cropIsActive: d, newCropIsBeingDrawn: f } = this.state, p = o ? this.renderCropSelection() : null, m = a("ReactCrop", i, d && "ReactCrop--active", s && "ReactCrop--disabled", c && "ReactCrop--locked", f && "ReactCrop--new-crop", o && t && "ReactCrop--fixed-aspect", o && r && "ReactCrop--circular-crop", o && u && "ReactCrop--rule-of-thirds", !this.dragStarted && o && !o.width && !o.height && "ReactCrop--invisible-crop", r && "ReactCrop--no-animate");
		return /* @__PURE__ */ e.createElement("div", {
			ref: this.componentRef,
			className: m,
			style: l
		}, /* @__PURE__ */ e.createElement("div", {
			ref: this.mediaRef,
			className: "ReactCrop__child-wrapper",
			onPointerDown: this.onComponentPointerDown
		}, n), o ? /* @__PURE__ */ e.createElement("svg", {
			className: "ReactCrop__crop-mask",
			width: "100%",
			height: "100%"
		}, /* @__PURE__ */ e.createElement("defs", null, /* @__PURE__ */ e.createElement("mask", { id: `hole-${this.instanceId}` }, /* @__PURE__ */ e.createElement("rect", {
			width: "100%",
			height: "100%",
			fill: "white"
		}), r ? /* @__PURE__ */ e.createElement("ellipse", {
			cx: `${o.x + o.width / 2}${o.unit}`,
			cy: `${o.y + o.height / 2}${o.unit}`,
			rx: `${o.width / 2}${o.unit}`,
			ry: `${o.height / 2}${o.unit}`,
			fill: "black"
		}) : /* @__PURE__ */ e.createElement("rect", {
			x: `${o.x}${o.unit}`,
			y: `${o.y}${o.unit}`,
			width: `${o.width}${o.unit}`,
			height: `${o.height}${o.unit}`,
			fill: "black"
		}))), /* @__PURE__ */ e.createElement("rect", {
			fill: "black",
			fillOpacity: .5,
			width: "100%",
			height: "100%",
			mask: `url(#hole-${this.instanceId})`
		})) : void 0, p);
	}
}, g = Math.PI / 180;
async function _(e, t, n, r = 1, i = 0) {
	let a = t.getContext("2d");
	if (!a) throw Error("No 2d context");
	let o = e.naturalWidth / e.width, s = e.naturalHeight / e.height, c = window.devicePixelRatio;
	t.width = Math.floor(n.width * o * c), t.height = Math.floor(n.height * s * c), a.scale(c, c), a.imageSmoothingQuality = "high";
	let l = n.x * o, u = n.y * s, d = i * g, f = e.naturalWidth / 2, p = e.naturalHeight / 2;
	a.save(), a.translate(-l, -u), a.translate(f, p), a.rotate(d), a.scale(r, r), a.translate(-f, -p), a.drawImage(e, 0, 0, e.naturalWidth, e.naturalHeight, 0, 0, e.naturalWidth, e.naturalHeight), a.restore();
}
function v(e) {
	return new Promise((t) => {
		e.toBlob(t);
	});
}
var y = "";
async function b(e, t, n = 1, r = 0) {
	let i = document.createElement("canvas");
	_(e, i, t, n, r);
	let a = await v(i);
	return a ? (y && URL.revokeObjectURL(y), y = URL.createObjectURL(a), y) : (console.error("Failed to create blob"), "");
}
//#endregion
export { h as Component, h as ReactCrop, h as default, o as areCropsEqual, c as centerCrop, i as clamp, a as cls, d as containCrop, l as convertToPercentCrop, u as convertToPixelCrop, _ as cropToCanvas, b as cropToImg, r as defaultCrop, s as makeAspectCrop, f as nudgeCrop };
