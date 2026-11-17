/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See License.txt in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

/** Pointer position, not CSS :hover, owns visibility while the bar animates. */
export class LibraryAutoHide {
	private shown = false;
	private disposed = false;
	private x = Infinity;
	private stamp = -Infinity;
	private focused = false;
	private protected = false;
	private timer: ReturnType<typeof setTimeout> | undefined;
	private pending: 'show' | 'hide' | undefined;
	constructor(private readonly width: () => number, private readonly change: (shown: boolean) => void,
		private readonly schedule: (fn: () => void, delay: number) => ReturnType<typeof setTimeout> = (fn, delay) => setTimeout(fn, delay),
		private readonly cancel: (timer: ReturnType<typeof setTimeout>) => void = timer => clearTimeout(timer)) { }

	pointer(x: number, stamp: number): boolean {
		// A webview move may arrive after a newer move at the window edge.
		if (!Number.isFinite(x) || !Number.isFinite(stamp) || stamp < this.stamp) { return false; }
		this.stamp = stamp;
		this.x = x;
		this.update();
		return true;
	}
	leave(stamp: number): void {
		if (stamp < this.stamp) { return; }
		this.stamp = stamp;
		this.x = Infinity;
		this.update();
	}
	protect(focused: boolean, protectedInteraction: boolean): void {
		this.focused = focused;
		this.protected = protectedInteraction;
		this.update();
	}
	private update(): void {
		if (this.disposed) { return; }
		if (this.focused) {
			this.stopTimer();
			this.setShown(true);
			return;
		}
		const next = this.shown
			? (this.protected || (this.x >= 0 && this.x <= this.width() + 40) ? undefined : 'hide')
			: (this.x >= 0 && this.x <= 6 ? 'show' : undefined);
		if (next === this.pending) { return; } // Outside motion must not restart the continuous-outside delay.
		this.stopTimer();
		if (next) {
			this.pending = next;
			this.timer = this.schedule(() => {
				this.timer = undefined;
				this.pending = undefined;
				this.setShown(next === 'show');
			}, next === 'show' ? 120 : 700);
		}
	}
	private setShown(value: boolean): void { if (this.shown !== value) { this.shown = value; this.change(value); } }
	private stopTimer(): void { if (this.timer !== undefined) { this.cancel(this.timer); } this.timer = undefined; this.pending = undefined; }
	dispose(): void { this.disposed = true; this.stopTimer(); }
}
