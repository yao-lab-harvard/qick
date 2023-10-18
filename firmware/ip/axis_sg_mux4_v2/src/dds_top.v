module dds_top (
	// Reset and clock.
	rstn			,
	clk				,

	// DDS output.
	dds_dout_o		,

	// Registers.
	PINC_REG		,
	GAIN_REG		,
	WE_REG
	);

/**************/
/* Parameters */
/**************/
// Number of parallel dds blocks.
parameter [31:0] N_DDS = 2;

/*********/
/* Ports */
/*********/
input						rstn;
input						clk;

output		[N_DDS*32-1:0]	dds_dout_o;

input		[31:0]			PINC_REG;
input		[15:0]			GAIN_REG;
input						WE_REG;

/********************/
/* Internal signals */
/********************/
// DDS input control.
reg							dds_tvalid_r;
wire 		[N_DDS*72-1:0]	dds_ctrl_int;
reg 		[N_DDS*72-1:0]	dds_ctrl_int_r;

// DDS output.
wire 		[31:0]			dds_dout	[0:N_DDS-1];
wire		[31:0]			dds_dout_la	[0:N_DDS-1];

// Product.
wire signed [15:0]			gain;
wire signed	[15:0]			prod_a_real	[0:N_DDS-1];
wire signed	[15:0]			prod_a_imag	[0:N_DDS-1];
wire signed	[31:0]			prod_real	[0:N_DDS-1];
wire signed	[31:0]			prod_imag	[0:N_DDS-1];
reg			[31:0]			prod_real_r1[0:N_DDS-1];
reg			[31:0]			prod_imag_r1[0:N_DDS-1];
wire		[15:0]			prod_real_q	[0:N_DDS-1];
wire		[15:0]			prod_imag_q	[0:N_DDS-1];
wire		[31:0]			prod		[0:N_DDS-1];
reg			[31:0]			prod_r1		[0:N_DDS-1];

/**********************/
/* Begin Architecture */
/**********************/
// Phase Control block.
phase_ctrl 
	#(
		.N_DDS	(N_DDS	)
	)
	phase_ctrl_i
	(
		// Reset and clock.
		.rstn		(rstn			),
		.clk		(clk			),

		// dds control.
		.dds_ctrl_o	(dds_ctrl_int	),

		// Registers.
		.PINC_REG	(PINC_REG		),
		.WE_REG		(WE_REG			)
		);

generate
genvar i;
	for (i=0; i<N_DDS; i=i+1) begin : GEN_dds
		/***********************/
		/* Block instantiation */
		/***********************/
		// DDS.
		// Latency: 10.
		dds_compiler_0 dds_i 
			(
		  		.aclk					(clk						),
		  		.s_axis_phase_tvalid	(dds_tvalid_r				),
		  		.s_axis_phase_tdata		(dds_ctrl_int_r[i*72 +: 72]	),
		  		.m_axis_data_tvalid		(							),
		  		.m_axis_data_tdata		(dds_dout[i]				)
			);

		// Latency for dds_dout.
		latency_reg
			#(
				.N(4),
				.B(32)
			)
			dds_dout_latency_reg_i
			(
				.rstn	(rstn			),
				.clk	(clk			),
		
				.din	(dds_dout[i]	),
				.dout	(dds_dout_la[i]	)
			);

		// Product.
		assign prod_a_real	[i] = dds_dout_la[i][15:0];
		assign prod_a_imag	[i] = dds_dout_la[i][31:16];
		assign prod_real	[i] = prod_a_real[i]*gain;
		assign prod_imag	[i] = prod_a_imag[i]*gain;
		assign prod_real_q	[i] = prod_real_r1[i][30 -: 16];
		assign prod_imag_q	[i] = prod_imag_r1[i][30 -: 16];
		assign prod			[i]	= {prod_imag_q[i], prod_real_q[i]};

		// Registers.
		always @(posedge clk) begin
			if (~rstn) begin
				// Product.
				prod_real_r1	[i]	<= 0;
				prod_imag_r1	[i]	<= 0;
				prod_r1			[i]	<= 0;
			end
			else begin
				// Product.
				prod_real_r1	[i]	<= prod_real	[i];
				prod_imag_r1	[i]	<= prod_imag	[i];
				prod_r1			[i]	<= prod			[i];
			end
		end

		/***********/
		/* Outputs */
		/***********/
		assign dds_dout_o[i*32 +: 32] = prod_r1[i];

	end
endgenerate 

// Gain.
assign gain = GAIN_REG;

// Registers.
always @(posedge clk) begin
	if (~rstn) begin
		// DDS intput control.
		dds_tvalid_r		<= 0;
		dds_ctrl_int_r		<= 0;
	end
	else begin
		// DDS intput control.
		dds_tvalid_r	<= 1;
		dds_ctrl_int_r	<= dds_ctrl_int;
	end
end

endmodule

